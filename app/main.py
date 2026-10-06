from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import auth, portability, repo, starter
from .db import MAX_VALUE, connect, db_path, iso, parse_day
from .rules import day_label, format_number, streak_days

BASE_DIR = Path(__file__).resolve().parent.parent

ENV_USER = "DIARIO_USER"
ENV_PASSWORD = "DIARIO_PASSWORD"
ENV_SECURE_COOKIE = "DIARIO_SECURE_COOKIE"
PUBLIC_PATHS = {"/login", "/healthz", "/manifest.webmanifest", "/favicon.ico", "/sw.js"}
PUBLIC_PREFIXES = ("/static/",)

credentials_announced = False
throttle = auth.LoginThrottle()


def ensure_user(conn) -> None:
    global credentials_announced
    auth.purge_expired_sessions(conn)
    if auth.count_users(conn):
        return
    username = os.environ.get(ENV_USER) or "admin"
    password = os.environ.get(ENV_PASSWORD) or auth.random_password()
    auth.create_user(conn, username, password)
    if credentials_announced:
        return
    credentials_announced = True
    banner = [
        "",
        "  ┌─ Diario de acciones: acceso creado ─────────────────────────",
        f"  │  usuario: {username}",
        f"  │  contraseña: {password}",
        "  │  Guarda esta contraseña: solo se muestra ahora.",
        "  │  Para cambiarla:  python manage.py passwd",
        "  └─────────────────────────────────────────────────────────────",
        "",
    ]
    print("\n".join(banner), flush=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    conn = connect()
    try:
        ensure_user(conn)
        portability.backup_if_needed(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="Diario de acciones", docs_url="/api/docs", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

templates.env.filters["num"] = format_number


def get_conn():
    conn = connect()
    try:
        ensure_user(conn)
        yield conn
    finally:
        conn.close()


def secure_cookies(request: Request) -> bool:
    if os.environ.get(ENV_SECURE_COOKIE) == "1":
        return True
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" not in accept


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return origin.split("://")[-1] == request.headers.get("host")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if (
        path in PUBLIC_PATHS
        or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)
        or request.method == "OPTIONS"
    ):
        return await call_next(request)

    if request.method not in ("GET", "HEAD") and not same_origin(request):
        return JSONResponse({"error": "Peticion de origen desconocido"}, status_code=403)

    conn = connect()
    try:
        token = request.cookies.get(auth.COOKIE_NAME)
        user = auth.user_for_token(conn, token)
    finally:
        conn.close()

    if user:
        request.state.user = user
        return await call_next(request)

    if wants_html(request):
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=f"/login?next={target}", status_code=303)
    return JSONResponse({"error": "Sesion caducada"}, status_code=401)


def current_user(request: Request) -> dict | None:
    conn = connect()
    try:
        return auth.user_for_token(conn, request.cookies.get(auth.COOKIE_NAME))
    finally:
        conn.close()


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: str | None = None):
    if current_user(request):
        return RedirectResponse(url=next or "/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error, "next": next or "/", "username": os.environ.get(ENV_USER, "")},
    )


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    ip = request.client.host if request.client else "?"
    conn = connect()
    try:
        ensure_user(conn)
        if throttle.blocked(ip):
            return _login_error(request, next, "Demasiados intentos. Espera unos minutos.", status=429)
        user = auth.authenticate(conn, username, password)
        if not user:
            throttle.fail(ip)
            return _login_error(request, next, "Usuario o contraseña incorrectos")
        throttle.succeed(ip)
        token, expires = auth.create_session(conn, user["id"], request.headers.get("user-agent"))
        destination = next if next.startswith("/") else "/"
        response = RedirectResponse(url=destination, status_code=303)
        response.set_cookie(
            auth.COOKIE_NAME,
            token,
            max_age=auth.SESSION_DAYS * 24 * 3600,
            httponly=True,
            samesite="lax",
            secure=secure_cookies(request),
            path="/",
        )
        return response
    finally:
        conn.close()


def _login_error(request: Request, next: str, message: str, status: int = 401) -> HTMLResponse:
    response = templates.TemplateResponse(
        request,
        "login.html",
        {"error": message, "next": next or "/", "username": ""},
        status_code=status,
    )
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


@app.post("/logout")
def logout(request: Request):
    conn = connect()
    try:
        token = request.cookies.get(auth.COOKIE_NAME)
        if token:
            auth.delete_session(conn, token)
    finally:
        conn.close()
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


def safe_day(value: str | None, fallback: date | None = None) -> date:
    try:
        return parse_day(value, fallback)
    except ValueError:
        return fallback or date.today()


def back(target: str, error: str | None = None, day: str | None = None) -> RedirectResponse:
    url = target
    params = []
    if error:
        params.append(f"error={error}")
    if day:
        params.append(f"day={day}")
    if params:
        url = f"{target}?{'&'.join(params)}"
    return RedirectResponse(url=url, status_code=303)


def day_context(conn, day: date) -> dict:
    view = repo.day_view(conn, day)
    today = date.today()
    return {
        "day": day,
        "day_str": iso(day),
        "day_name": day_label(day),
        "is_today": day == today,
        "prev_day": day - timedelta(days=1),
        "next_day": day + timedelta(days=1),
        "items": view["items"],
        "summary": view["summary"],
        "note": view["note"],
        "no_tasks": not view["items"],
        "areas": repo.areas(conn),
        "error": None,
    }


def render_day(request: Request, conn, day: date, error: str | None = None) -> HTMLResponse:
    context = day_context(conn, day)
    context["error"] = error
    return templates.TemplateResponse(request, "day.html", context)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, day: str | None = None, error: str | None = None, conn=Depends(get_conn)):
    target = safe_day(day)
    return render_day(request, conn, target, error)


class EntryPayload(BaseModel):
    task_id: int
    day: str
    value: float
    note: str | None = None


class DayNotePayload(BaseModel):
    day: str
    content: str = Field(default="")


def apply_entry(conn, payload: EntryPayload) -> dict:
    target = safe_day(payload.day)
    repo.set_entry(conn, payload.task_id, iso(target), payload.value, payload.note)
    view = repo.day_view(conn, target)
    return {
        "values": {str(item["id"]): item["value"] for item in repo.flatten(view["items"])},
        "summary": view["summary"],
        "note": view["note"],
    }


@app.post("/api/entry")
def api_entry(payload: EntryPayload, conn=Depends(get_conn)):
    try:
        return JSONResponse(apply_entry(conn, payload))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/entrada")
def entry_form(
    task_id: int = Form(...),
    day: str = Form(...),
    value: str | None = Form(None),
    quick: str | None = Form(None),
    note: str | None = Form(None),
    conn=Depends(get_conn),
):
    target = safe_day(day)
    raw = quick if quick is not None else value
    if raw is None or raw == "":
        return back("/", error="Falta el valor de la tarea", day=iso(target))
    try:
        repo.set_entry(conn, task_id, iso(target), raw, note)
    except ValueError as exc:
        return back("/", error=str(exc), day=iso(target))
    return RedirectResponse(url=f"/?day={iso(target)}", status_code=303)


@app.post("/api/nota-dia")
def api_day_note(payload: DayNotePayload, conn=Depends(get_conn)):
    target = safe_day(payload.day)
    repo.save_day_note(conn, iso(target), payload.content)
    return {"ok": True, "day": iso(target)}


@app.post("/nota-dia")
def day_note_form(day: str = Form(...), content: str = Form(""), conn=Depends(get_conn)):
    target = safe_day(day)
    repo.save_day_note(conn, iso(target), content)
    return RedirectResponse(url=f"/?day={iso(target)}", status_code=303)


@app.get("/tareas", response_class=HTMLResponse)
def tasks_page(request: Request, error: str | None = None, conn=Depends(get_conn)):
    tasks = repo.all_tasks(conn)
    kids: dict[int | None, list[dict]] = {}
    for task in tasks:
        kids.setdefault(task["parent_id"], []).append(task)
    roots = kids.get(None, [])
    today = iso(date.today())
    active: list[dict] = []
    past_agenda: list[dict] = []
    for root in roots:
        children = kids.get(root["id"], [])
        root["children"] = children
        for child in children:
            child["children"] = []
        root["active_children"] = sum(
            1 for c in children if c["archived_on"] is None or c["archived_on"] > today
        )
        if not root.get("repeat", 1) and root["start_date"] < today:
            past_agenda.append(root)
        else:
            active.append(root)
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {
            "roots": active,
            "past_agenda": sorted(past_agenda, key=lambda t: t["start_date"], reverse=True),
            "areas": repo.areas(conn),
            "today": today,
            "archived": [t for t in tasks if t["archived_on"] is not None and t["archived_on"] <= today],
            "error": error,
        },
    )


@app.post("/tareas/crear")
def create_task_form(
    name: str = Form(...),
    area: str | None = Form(None),
    note: str | None = Form(None),
    parent_id: str | None = Form(None),
    start_date: str | None = Form(None),
    day: str | None = Form(None),
    repeat: str = Form("1"),
    return_to: str | None = Form(None),
    conn=Depends(get_conn),
):
    parent = int(parent_id) if parent_id not in (None, "", "None") else None
    start = iso(safe_day(start_date)) if start_date else iso(safe_day(day))
    one_shot = repeat in ("0", "false", "off")
    try:
        repo.create_task(conn, name, area, note, parent, start, repeat=not one_shot)
    except ValueError as exc:
        return back("/tareas", error=str(exc))
    return RedirectResponse(url=_safe_return(return_to), status_code=303)


def _safe_return(path: str | None) -> str:
    if path and path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path
    return "/tareas"


@app.get("/tareas/base", response_class=HTMLResponse)
def base_tasks_page(
    request: Request,
    day: str | None = None,
    creadas: int | None = None,
    omitidas: int | None = None,
    error: str | None = None,
    conn=Depends(get_conn),
):
    return templates.TemplateResponse(
        request,
        "tareas_base.html",
        {
            "base_tasks": starter.BASE_TASKS,
            "areas": repo.areas(conn),
            "error": error,
            "day": day,
            "creadas": creadas,
            "omitidas": omitidas,
            "count": len(repo.all_tasks(conn)),
        },
    )


@app.post("/tareas/base")
async def base_tasks_create(
    request: Request,
    other: str = Form(""),
    day: str | None = Form(None),
    conn=Depends(get_conn),
):
    form = await request.form()
    selected = [value for value in form.getlist("tasks") if value]
    report = starter.load_base(conn, selected, other, day and safe_day(day))
    if day:
        return RedirectResponse(url=f"/?day={iso(safe_day(day))}", status_code=303)
    params = f"creadas={len(report['created'])}&omitidas={len(report['skipped'])}"
    return RedirectResponse(url=f"/tareas/base?{params}", status_code=303)


@app.post("/tareas/{task_id}/editar")
def edit_task_form(
    task_id: int,
    name: str = Form(...),
    area: str | None = Form(None),
    note: str | None = Form(None),
    conn=Depends(get_conn),
):
    try:
        repo.update_task(conn, task_id, name, area, note)
    except ValueError as exc:
        return back("/tareas", error=str(exc))
    return RedirectResponse(url="/tareas", status_code=303)


@app.post("/tareas/{task_id}/archivar")
def archive_task_form(
    task_id: int,
    from_date: str | None = Form(None),
    day: str | None = Form(None),
    conn=Depends(get_conn),
):
    origin = iso(safe_day(from_date or day))
    try:
        repo.archive_task(conn, task_id, origin)
    except ValueError as exc:
        return back("/tareas", error=str(exc))
    return RedirectResponse(url="/tareas", status_code=303)


@app.post("/tareas/{task_id}/restaurar")
def restore_task_form(task_id: int, conn=Depends(get_conn)):
    repo.restore_task(conn, task_id)
    return RedirectResponse(url="/tareas", status_code=303)


@app.post("/tareas/{task_id}/borrar")
def delete_task_form(task_id: int, conn=Depends(get_conn)):
    try:
        repo.delete_task(conn, task_id)
    except ValueError as exc:
        return back("/tareas", error=str(exc))
    return RedirectResponse(url="/tareas", status_code=303)


@app.post("/tareas/{task_id}/quitar-hijas")
def remove_children_form(
    task_id: int,
    from_date: str | None = Form(None),
    day: str | None = Form(None),
    conn=Depends(get_conn),
):
    origin = iso(safe_day(from_date or day))
    repo.archive_children(conn, task_id, origin)
    return RedirectResponse(url="/tareas", status_code=303)


@app.post("/tareas/{task_id}/recuperar-hijas")
def restore_children_form(task_id: int, conn=Depends(get_conn)):
    repo.restore_children(conn, task_id)
    return RedirectResponse(url="/tareas", status_code=303)


@app.get("/estadisticas", response_class=HTMLResponse)
def stats_page(request: Request, days: int = 30, error: str | None = None, conn=Depends(get_conn)):
    days = max(7, min(365, days))
    today = date.today()
    rows = repo.history(conn, today, days)
    active_days = [r for r in rows if r["has_activity"]]
    avgs = [r["avg"] for r in rows if r["avg"] is not None]
    best = max(active_days, key=lambda r: r["avg"]) if active_days else None
    tracked_days = [r for r in rows if r["total"] > 0]
    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "days": days,
            "today": today,
            "rows": rows,
            "avgs": avgs,
            "avg": sum(avgs) / len(avgs) if avgs else None,
            "active_days": len(active_days),
            "best": best,
            "best_label": f"{best['day']} ({format_number(best['avg'])})" if best else "-",
            "streak": streak_days(rows, today),
            "completion": (
                sum(r["done"] for r in tracked_days) / sum(r["total"] for r in tracked_days)
                if tracked_days
                else None
            ),
            "areas": repo.area_breakdown(conn, today, days),
            "error": error,
        },
    )


@app.get("/api/resumen")
def api_summary(day: str | None = None, conn=Depends(get_conn)):
    view = repo.day_view(conn, safe_day(day))
    return {
        "values": {str(item["id"]): item["value"] for item in repo.flatten(view["items"])},
        "summary": view["summary"],
    }


@app.get("/healthz")
def healthz():
    return {"ok": True, "max_value": MAX_VALUE}


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse(
        {
            "name": "Diario de acciones",
            "short_name": "Diario",
            "description": "Diario diario de acciones con valores de 0 a 10",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#14161a",
            "theme_color": "#14161a",
            "lang": "es",
            "icons": [
                {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
                 "purpose": "maskable"},
            ],
            "shortcuts": [
                {"name": "Hoy", "url": "/"},
                {"name": "Tareas", "url": "/tareas"},
            ],
        },
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
def service_worker():
    body = (BASE_DIR / "static" / "sw.js").read_text(encoding="utf-8")
    return Response(body, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/api/export.json")
def export_json(conn=Depends(get_conn)):
    stamp = date.today().isoformat()
    return Response(
        portability.dumps(portability.export_json(conn)),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="diario-{stamp}.json"'},
    )


@app.get("/api/export.csv", response_class=PlainTextResponse)
def export_csv(conn=Depends(get_conn)):
    stamp = date.today().isoformat()
    return PlainTextResponse(
        portability.export_entries_csv(conn),
        headers={"Content-Disposition": f'attachment; filename="diario-{stamp}.csv"'},
    )


@app.get("/api/notas.csv", response_class=PlainTextResponse)
def export_notes(conn=Depends(get_conn)):
    stamp = date.today().isoformat()
    return PlainTextResponse(
        portability.export_notes_csv(conn),
        headers={"Content-Disposition": f'attachment; filename="diario-notas-{stamp}.csv"'},
    )


@app.post("/api/importar")
async def import_data(
    mode: str = "replace",
    file: UploadFile = File(...),
    conn=Depends(get_conn),
):
    import json

    try:
        payload = json.loads((await file.read()).decode("utf-8"))
        report = portability.import_json(conn, payload, mode=mode)
    except (ValueError, UnicodeDecodeError) as exc:
        return JSONResponse({"error": f"No se pudo importar: {exc}"}, status_code=400)
    return report


@app.get("/api/estado")
def api_status(conn=Depends(get_conn)):
    return portability.summary(conn)