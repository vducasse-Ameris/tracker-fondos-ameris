"""Publica el reporte HTML en un repositorio GitHub Pages.

Objetivo: que el equipo abra una URL fija (https://USUARIO.github.io/REPO/) que
se actualice sola con cada corrida diaria, sin reenviar archivos ni tener el
código. Cada publicación copia el reporte como `index.html`, hace commit y push;
GitHub Pages sirve la nueva versión en 1-2 minutos.

Configuración (UNA sola vez): crear un archivo `publicar.config` junto a este
script cuyo contenido sea la ruta del clon local del repo de Pages, por ejemplo:

    C:\\Users\\VicenteDucasse\\dashboard-fondos-pages

Ese repo debe tener Pages activado (Settings → Pages → Deploy from a branch →
main / root). Ver README, sección «Compartir con el equipo (GitHub Pages)».

Si el archivo de config no existe, la publicación se omite en silencio (el resto
del pipeline no se ve afectado).
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
from pathlib import Path

RAIZ = Path(__file__).parent
CONFIG = RAIZ / "publicar.config"


def _git(repo: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def publicar(reporte_html: Path) -> bool:
    """Copia el reporte a <repo>/index.html y hace commit + push.

    Devuelve True si publicó una versión nueva; False si no está configurado, no
    hubo cambios, o falló el push (no lanza excepción para no cortar la corrida).
    """
    if not CONFIG.exists():
        print("Publicación web: no configurada (falta publicar.config) — se omite.")
        return False
    repo = Path(CONFIG.read_text(encoding="utf-8").strip().strip('"'))
    if not (repo / ".git").exists():
        print(f"Publicación web: '{repo}' no es un repositorio git — se omite.")
        return False

    shutil.copyfile(reporte_html, repo / "index.html")
    _git(repo, "add", "index.html")
    if not _git(repo, "status", "--porcelain").stdout.strip():
        print("Publicación web: sin cambios (ya estaba al día).")
        return False

    marca = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    commit = _git(repo, "commit", "-m", f"Actualiza dashboard {marca}")
    if commit.returncode != 0:
        print(f"Publicación web: ERROR en commit:\n{commit.stderr.strip()}")
        return False
    push = _git(repo, "push")
    if push.returncode != 0:
        # primer push de una rama sin upstream configurado (repo recién clonado)
        push = _git(repo, "push", "-u", "origin", "HEAD")
    if push.returncode != 0:
        print(f"Publicación web: ERROR en push (¿credenciales?):\n{push.stderr.strip()}")
        return False
    print("Publicación web: dashboard actualizado en GitHub Pages.")
    return True


if __name__ == "__main__":
    # publicación manual: usa el último reporte generado
    publicar(RAIZ / "reportes" / "reporte.html")
