# Registro de cambios

Los cambios notables de krunner-abbr. Las versiones publicadas están en
[GitHub](https://github.com/nivaca/krunner-abbr/releases) y
[Codeberg](https://codeberg.org/nivaca/krunner-abbr/releases).

## Sin publicar

- `build_rpm.py` copia también el RPM más reciente en `dist/`, en lugar
  de los anteriores que hubiera allí.
- Los archivos compilados de `__pycache__` ya no están en el repositorio,
  y `dist/` se ignora.

## 2.0.1 — 2026-11-17

- Se necesita Python 3.14 o posterior (antes, 3.11): el runner, el
  diálogo de configuración y los scripts de construcción se detienen con
  un mensaje en un Python anterior, y el RPM lo exige.
- Un filtro nuevo, `utf16`, convierte unidades de código UTF-16 en texto:
  `\uD83D\uDE00` dentro de un texto, o solo las unidades (`D83D DE00`,
  `U+00E9`), se convierten en 😀 y é. Los pares sustitutos se unen.
- La versión publicada trae un zip, `krunner-abbr-2.0.1.zip`, con el
  programa entero: se descomprime y se ejecuta `./install.sh` en su
  carpeta.

## 2.0 — 2026-10-29

- **Pegar con `|paste`.** Enter copia el resultado; para pegarlo además,
  se escribe `|paste` al final de la consulta (`ex isod|paste`, `ex
  |upper|paste text`). El resultado dice "Enter pastes" antes de pulsar
  Enter. Desaparecen el ajuste `enter_pastes` y el botón Copy and paste.
- El pegado pasa por el portal de "control remoto" de Plasma, así que no
  hace falta nada más: Plasma pide permiso una vez, y el permiso se
  guarda en `~/.local/state/krunner-abbr/portal-token`. `ydotool` se usa
  solo si el portal no funciona; `wtype` ya no se usa.
- Los resultados de varias líneas, o los largos, se muestran en KRunner
  en hasta cinco líneas, y un resultado que sea una dirección web puede
  arrastrarse al navegador o a un documento.
- La ayuda de KRunner (`?`) muestra la sintaxis de este plugin (el runner
  usa la API DBus2).
- Cuando se cambia la palabra clave en el diálogo de configuración,
  KRunner se reinicia, porque solo pide la palabra clave al arrancar.
- El diálogo de configuración señala los problemas de una abreviatura y
  de la palabra clave, y una abreviatura eliminada puede recuperarse con
  deshacer.
- `./install.sh --clean` reemplaza la configuración por la de ejemplo y
  conserva el archivo anterior como `abbreviations.toml.bak-` seguido de
  la fecha y la hora.
- `build_zip.py` hace un zip del programa.

## 1.1 — 2026-10-29

- **Un paquete para Fedora.** `./build_rpm.py` hace un RPM (también
  `--srpm`, `--release N`, `--mock CHROOT` y `--copr PROJECT`), que pone
  el programa en `/usr/share/krunner-abbr` con una orden `krunner-abbr`.
  Los archivos del paquete están en `packaging/`.
- El programa, que era un solo archivo, se divide en módulos:
  `krunner_abbr_config.py`, `_desktop`, `_filters`, `_match`, `_service`
  y `_template`.

## 1.0 — 2026-10-28

- Primera versión: un plugin para KRunner (KDE Plasma 6) que expande
  abreviaturas. Al escribir la palabra clave y una abreviatura (`ex isod`)
  la expansión queda en el portapapeles, y también puede pegarse en la
  ventana en la que se estaba trabajando. Las abreviaturas se guardan en
  un solo archivo TOML y pueden dar fechas en cualquier formato e idioma,
  texto del portapapeles, formularios que piden datos, órdenes de la
  shell, filtros de mayúsculas y de comillas, ayudas para latín y
  español, y enlaces.
- Un diálogo de configuración (PySide6), páginas de ayuda y de lista, un
  importador de archivos de espanso, una línea de órdenes, e `install.sh`
  y `uninstall.sh`.
- La palabra clave por defecto es `ex` (antes era `abbr`).
- GPL-3.0-or-later.
