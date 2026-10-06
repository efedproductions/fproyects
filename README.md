# Diario de acciones

Web privada para llevar un diario diario de acciones: cada día marcas el valor de cada
tarea (0-10) y escribes una nota. Sin cuentas, sin nube, sin dependencias externas: un
solo fichero SQLite.

- **0 significa que no se hizo**, no que esté pendiente. 5 es "suficientemente bien";
  el 10 es raro a propósito.
- Una tarea nueva **aparece todos los días siguientes con un 0** hasta que la borres.
- **Borrar** una tarea la deja de mostrar a partir de ese día; el historial ya escrito se
  conserva. "Borrar para siempre" elimina también su historial.
- **Dos niveles como máximo**: una tarea y sus subtareas.
- Si una tarea tiene subtareas, su valor **se calcula**: la media de las subtareas con
  valor > 0. Si ninguna se hizo, vale 0. Un 0 nunca baja la media: hacer solo el baño un
  día da un "Limpieza en casa" de 8, no de 4. El porcentaje de subtareas hechas va aparte.
- Cada tarea tiene **un área** (aprender, ocio, salud, casa, terapia, religión...).

## Empezar en 30 segundos

Con Docker (recomendado):

```bash
docker run -d --name diario --restart unless-stopped \
  -v "$PWD/diario-data:/data" -p 127.0.0.1:8300:8000 diario:local
```

Sin Docker, en un ordenador con Python 3.11+:

```bash
./install.sh
./run.sh
```

La primera vez se crea un usuario y una contraseña; se muestran en la consola (Docker:
`docker logs diario`). Después abre <http://127.0.0.1:8300>.

Página: `/` el día, `/tareas` para crear y dividir, `/estadisticas` para el historial.

## Instalarlo en el móvil

No hay app en las tiendas: se instala desde el navegador, en un minuto y sin cuentas de
desarrollador.

**Android (Chrome)**: abre la dirección del diario → menú ⋮ → *Instalar aplicación* (o
*Añadir a pantalla de inicio*).
**iPhone (Safari)**: abre la dirección → botón Compartir → *Añadir a pantalla de inicio*.
**Ordenador (Chrome/Edge)**: icono de instalar en la barra de direcciones.

Queda con icono propio, se abre a pantalla completa y muestra una pantalla de aviso si
estás sin cobertura. Importante: **la dirección del diario solo la usas tú**, con tu
usuario y contraseña.

## Desplegarlo en tu VPS (Game Panel)

Todo en `/opt/diario`, con el repositorio copiado por SSH o `git clone`.

### 1. Base: HTTP en tu IP

```bash
cd /opt/diario
cp .env.example .env      # edita DIARIO_USER y DIARIO_PASSWORD
docker compose up -d --build
docker compose logs app | grep contraseña
```

Queda en `http://<IP-DEL-SERVIDOR>:8300`. **Solo escucha en 127.0.0.1 dentro del servidor**,
así que para verlo desde fuera hay que abrir el puerto 8300 en el panel o:

```bash
# si prefieres un túnel SSH desde tu ordenador, sin abrir nada a internet
ssh -L 8300:127.0.0.1:8300 root@<IP-DEL-SERVIDOR>
```

### 2. Con dominio: HTTPS automático (recomendado en internet)

Apunta un dominio (por ejemplo `diario.tudominio.com`) a `<IP-DEL-SERVIDOR>`, abre los
puertos 80 y 443, y:

```bash
cd /opt/diario
echo "DOMAIN=diario.tudominio.com" >> .env
docker compose -f compose.yaml -f compose.https.yaml up -d
```

Caddy pide el certificado a Let's Encrypt y lo renueva solo. Con HTTPS ya activo, pon
`DIARIO_SECURE_COOKIE=1` en `.env` y reinicia (`docker compose up -d`).

Sin dominio público puedes usar `tls internal` en `deploy/Caddyfile`, pero ese certificado
no lo confiará ningún navegador: úsalo solo dentro de tu red.

### 3. Tu red de casa: Tailscale

Si algún día quieres servirlo desde un ordenador de casa, instala
[Tailscale](https://tailscale.com) en ambos. Es una VPN: el ordenador queda accesible en
`http://100.x.y.z:8300` solo para tus dispositivos, sin abrir puertos ni certificates.

## Datos: copia, exportación e importación

Todo vive en un fichero: `data/diario.db` (Docker: `./data/diario.db`).

- **Backup automático**: al arrancar, si el último es de hace más de 24 h, se crea uno en
  `data/backups/` y se conservan los 14 últimos.
- **Exportar** desde la web: `/api/export.json` (copia fiel), `/api/export.csv` (hoja de
  cálculo con día, tarea, área, valor y nota) y `/api/notas.csv`.
- **Exportar/importar** por consola:

```bash
python manage.py backup                                   # copia ahora
python manage.py export --format json --out copia.json    # o csv
python manage.py import copia.json --mode merge           # no pisa lo que ya hay
python manage.py import copia.json --mode replace         # rehace todo
python manage.py status                                   # resumen
```

`--mode merge` combina por tarea y día: no duplica y no sobrescribe. Antes de importar se
guarda un backup automáticamente.

## Seguridad

- Contraseña única por despliegue, guardada con PBKDF2-SHA256 (260 000 iteraciones) y sal
  por usuario. No se puede leer desde la base de datos.
- Sesión por cookie `HttpOnly` + `SameSite=Lax`, 30 días; el cambio de contraseña cierra
  las sesiones abiertas.
- Ocho intentos fallidos bloquean el acceso de esa IP durante 10 minutos.
- Las peticiones `POST` desde otro origen se rechazan (CSRF).
- Sin HTTPS la contraseña viajaría en claro: por eso, para uso en internet, usa el paso 2
  con dominio.
- Cambia la contraseña cuando quieras: `python manage.py passwd` (en Docker:
  `docker compose exec app python manage.py passwd`).

## Compartir el código

Código abierto con licencia MIT (`LICENSE`) en
<https://github.com/efedproductions/fproyects>. Solo hay código: nada de datos,
contraseñas ni historiales.

Para publicar un cambio:

```bash
git add -A
git status          # revisa que no se cuela nada privado
git commit -m "Descripción del cambio"
git push
```

## Privacidad

- **Cada instalación es su propio servidor**: los datos solo viven en el ordenador o el
  contenedor donde corre la app. Si otra persona instala el diario, su historial queda en
  su máquina; no pasa por la tuya ni por ningún servicio tercero.
- El repositorio contiene **solo código**: no hay datos, contraseñas, historiales ni
  nada extraído de una instancia real.
- **Sin telemetría, sin analíticas, sin peticiones a internet**. Las únicas llamadas son
  del navegador a su propia dirección, y de Let's Encrypt si usas el proxy HTTPS.
- **Se comparte el código, no la URL**: quien lo use debe montarse su propia instancia
  con `install.sh` o `docker compose`. No dejes la puerta abierta con una contraseña
  compartida.

## Para desarrollar

```bash
python -m venv venv && venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest -q          # 39 tests
venv/bin/python -m uvicorn app.main:app --reload
```

Estructura: `app/db.py` (esquema), `app/repo.py` (tareas y días), `app/rules.py` (la
media sin ceros, en funciones puras), `app/auth.py`, `app/portability.py` (copias y
exportación), `app/main.py` (rutas), `templates/`, `static/`.

Variables de entorno: `DIARIO_DB` (ruta de la base), `DIARIO_USER`, `DIARIO_PASSWORD`,
`DIARIO_PORT`, `DIARIO_HOST`, `DIARIO_SECURE_COOKIE`.