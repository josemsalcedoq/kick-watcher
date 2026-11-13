# kick-watcher

Mantiene un stream de Kick reproduciéndose en un Chrome real, con tu propia cuenta, hasta que Kick marca el tiempo de visualización necesario para los cofres de recompensa (por ejemplo el *Sunset Reward Case* de 180 minutos), los reclama y duerme hasta el reinicio diario.

Pensado para un servidor Linux sin pantalla (probado en Amazon Linux 2023, t3.small).

## Cómo funciona

- `login.py` se corre en tu PC: abre una ventana del navegador en kick.com, inicias sesión a mano y guarda la sesión en `state.json`.
- `watcher.py run` corre en el servidor: importa esa sesión, elige un stream en vivo con muchos espectadores (salta casino y contenido +18), comprueba que el video avance y consulta el progreso en la API de Kick (`/api/v1/gamification/challenges`).
- Cuando un cofre queda desbloqueado, abre el diálogo de recompensas y pulsa **Reclamar**. Después duerme hasta que termine la ventana diaria (00:00 UTC).
- El estado queda en `status.json` (fase, stream actual, minutos por cofre, último reclamo), fácil de leer desde otro bot o script.

## Instalación en el servidor

Hace falta Google Chrome (el Chromium de Playwright no trae H.264 y el video no reproduce), Xvfb y Python 3.11+.

```sh
sudo dnf -y install https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm \
    xorg-x11-server-Xvfb python3.11 python3.11-pip libatomic
sudo useradd --system --create-home --home-dir /var/lib/kick-watcher --shell /sbin/nologin kickwatcher
sudo mkdir -p /opt/kick-watcher && sudo chown kickwatcher: /opt/kick-watcher
sudo -u kickwatcher python3.11 -m venv /opt/kick-watcher/.venv
sudo -u kickwatcher /opt/kick-watcher/.venv/bin/pip install playwright
sudo install -o kickwatcher -g kickwatcher -m 644 watcher.py /opt/kick-watcher/
sudo install -m 644 kick-watcher.service /etc/systemd/system/
```

## Sesión

En tu PC (con `pip install playwright` y Microsoft Edge):

```sh
python login.py
```

Inicia sesión en la ventana que se abre. Luego copia `state.json` al servidor:

```sh
scp state.json servidor:/tmp/
ssh servidor 'sudo install -o kickwatcher -g kickwatcher -m 600 /tmp/state.json /var/lib/kick-watcher/ && rm /tmp/state.json'
sudo systemctl enable --now kick-watcher
```

`state.json` contiene tu sesión de Kick: trátalo como una contraseña y no lo subas a ningún repositorio.

## Uso

```sh
journalctl -u kick-watcher -f           # registro
cat /var/lib/kick-watcher/status.json   # progreso del día
```

Otros modos, útiles para depurar (dejan capturas en `debug/`):

```sh
watcher.py probe [canal]   # abre un stream ~40 s
watcher.py inspect         # abre el diálogo de recompensas y registra las llamadas a la API
```

## Aviso

Úsalo solo con tu propia cuenta. Kick puede descalificar participaciones a su discreción según sus [términos de Drops](https://kick.com/kick-drops-terms); el riesgo de usarlo es tuyo. No está afiliado a Kick.
