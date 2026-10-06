"""Utilidades de linea de comandos para el diario.

    python manage.py status
    python manage.py user add ana
    python manage.py passwd
    python manage.py backup
    python manage.py export --format json
    python manage.py import copia.json --mode merge
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import auth, portability  # noqa: E402
from app.db import connect, db_path  # noqa: E402


def prompt(label: str, default: str = "") -> str:
    if not sys.stdin.isatty():
        return default
    try:
        return input(label).strip() or default
    except EOFError:
        return default


def secret(label: str) -> str:
    if not sys.stdin.isatty():
        return ""
    return getpass.getpass(label)


def cmd_status(conn, args) -> None:
    print(f"Base de datos: {db_path()}")
    data = portability.summary(conn)
    for key, value in data.items():
        print(f"  {key.replace('_', ' ')}: {value}")
    folder = portability.backup_dir()
    backups = sorted(folder.glob("diario-*.db"))
    if backups:
        print(f"Ultimo backup: {backups[-1].name} ({len(backups)} guardados en {folder})")
    else:
        print("Todavia no hay backups.")


def cmd_user(conn, args) -> None:
    if not auth.get_user(conn, args.username):
        password = args.password or secret("Contrasena: ")
        try:
            auth.create_user(conn, args.username, password)
        except auth.AuthError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(f"Usuario «{args.username}» creado.")
    else:
        print(f"El usuario «{args.username}» ya existe. Usa 'passwd' para cambiar su contrasena.")
    users = [r["username"] for r in conn.execute("SELECT username FROM app_user").fetchall()]
    print(f"Usuarios: {', '.join(users)}")


def cmd_passwd(conn, args) -> None:
    username = args.username or prompt("Usuario: ")
    if not auth.get_user(conn, username):
        print(f"El usuario «{username}» no existe", file=sys.stderr)
        raise SystemExit(1)
    password = args.password or secret("Contrasena nueva: ")
    try:
        auth.set_password(conn, username, password)
    except auth.AuthError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Contrasena de «{username}» cambiada. Se han cerrado sus sesiones abiertas.")


def cmd_ensure_user(conn, args) -> None:
    if auth.count_users(conn):
        print("El acceso ya existe: no se ha cambiado nada.")
        return
    username = args.username or prompt("Usuario [admin]: ", "admin")
    password = args.password or auth.random_password()
    try:
        auth.create_user(conn, username, password)
    except auth.AuthError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("")
    print("  Acceso creado")
    print(f"    usuario:     {username}")
    print(f"    contraseña:  {password}")
    print("    Guarda esta contraseña: no se vuelve a mostrar.")
    print("    Para cambiarla:  python manage.py passwd")
    print("")


def cmd_backup(conn, args) -> None:
    path = portability.create_backup(conn)
    print(f"Backup creado: {path}")


def _write(text: str, out: str | None) -> None:
    if not out:
        print(text)
        return
    Path(out).write_text(text, encoding="utf-8")
    print(f"Guardado en {out}")


def cmd_export(conn, args) -> None:
    if args.format == "json":
        _write(portability.dumps(portability.export_json(conn)), args.out)
    elif args.format == "csv":
        _write(portability.export_entries_csv(conn), args.out)
    else:
        _write(portability.export_notes_csv(conn), args.out)


def cmd_import(conn, args) -> None:
    path = Path(args.file)
    if not path.exists():
        print(f"No existe el fichero {path}", file=sys.stderr)
        raise SystemExit(1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if args.backup:
        print(f"Backup previo: {portability.create_backup(conn)}")
    try:
        report = portability.import_json(conn, payload, mode=args.mode)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("Importacion completada:")
    for key, value in report.items():
        print(f"  {key.replace('_', ' ')}: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Utilidades del diario de acciones")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Resumen de la base de datos").set_defaults(func=cmd_status)

    user = sub.add_parser("user", help="Crear un usuario")
    user.add_argument("action", choices=["add"], help="add")
    user.add_argument("username")
    user.add_argument("--password", help="Si se omite, se pregunta sin mostrar")
    user.set_defaults(func=cmd_user)

    passwd = sub.add_parser("passwd", help="Cambiar la contrasena de un usuario")
    passwd.add_argument("--username")
    passwd.add_argument("--password")
    passwd.set_defaults(func=cmd_passwd)

    ensure = sub.add_parser("ensure-user", help="Crear el acceso solo si no existe todavia")
    ensure.add_argument("--username")
    ensure.add_argument("--password")
    ensure.set_defaults(func=cmd_ensure_user)

    sub.add_parser("backup", help="Crear una copia de seguridad ahora").set_defaults(func=cmd_backup)

    export = sub.add_parser("export", help="Exportar los datos")
    export.add_argument("--format", choices=["json", "csv", "notes"], default="json")
    export.add_argument("--out", help="Fichero de salida; si se omite, va a la consola")
    export.set_defaults(func=cmd_export)

    importer = sub.add_parser("import", help="Importar una exportacion json")
    importer.add_argument("file")
    importer.add_argument("--mode", choices=["replace", "merge"], default="merge")
    importer.add_argument("--backup", action="store_true", default=True)
    importer.add_argument("--no-backup", dest="backup", action="store_false")
    importer.set_defaults(func=cmd_import)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    conn = connect()
    try:
        args.func(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()