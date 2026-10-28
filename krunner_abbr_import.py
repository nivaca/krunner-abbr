"""Convert espanso match files (YAML) into krunner-abbr abbreviations.

    krunner_abbr.py --import-espanso base.yml            print the TOML
    krunner_abbr.py --import-espanso base.yml --write    append it to your config

Supported: triggers, replace / markdown / html, $|$ (cursor), forms and form fields, and
the variables echo, date (format and offset), clipboard, shell, script, choice, match and
form. Matches using anything else (regex triggers, images, random, global variables…)
are skipped with a note on stderr, as are triggers that already exist.
"""

import re
import shlex
import shutil
import sys
from pathlib import Path

import krunner_abbr as k


class Skip(Exception):
    """This match can't be converted; the message says why."""


def literal(text: str) -> str:
    return text.replace("{", "{{").replace("}", "}}")


def key_for(trigger: str) -> str:
    """':date' -> 'date'; KRunner keys are one word."""
    key = re.sub(r"^\W+", "", trigger)
    if not key or re.search(r"\s", key):
        raise Skip(f"trigger “{trigger}” can't be an abbreviation name")
    return key


def no_braces(text: str, what: str) -> str:
    if "{" in text or "}" in text:
        raise Skip(f"{what} contains {{ or }}, which placeholders can't hold")
    return text


def option_list(values: list) -> str:
    """Options for {choose}: a, b, "c, d"."""
    out = []
    for v in values:
        v = str(v["id"] if isinstance(v, dict) else v)
        no_braces(v, "a choice")
        out.append(f'"{v}"' if "," in v and '"' not in v else v)
    return ", ".join(out)


def field(name: str, spec: dict) -> str:
    """A form field as a prompt placeholder."""
    no_braces(name, "a form field name")
    spec = spec or {}
    if spec.get("type") in ("choice", "list"):
        return f"{{choose:{name}={option_list(spec.get('values', []))}}}"
    if spec.get("multiline"):
        return f"{{asktext:{name}}}"
    default = no_braces(str(spec.get("default", "")), "a default")
    return f"{{ask:{name}={default}}}" if default else f"{{ask:{name}}}"


def form_text(layout: str, fields: dict) -> str:
    """espanso form layout ([[field]]) as a template."""
    out, pos = [], 0
    for m in re.finditer(r"\[\[\s*([\w.-]+)\s*\]\]", layout):
        out += [literal(layout[pos:m.start()]), field(m[1], fields.get(m[1]))]
        pos = m.end()
    return "".join(out) + literal(layout[pos:])


def variable(var: dict) -> str | dict:
    """A variable's placeholder; a form variable gives {field: placeholder}."""
    kind, params = var.get("type"), var.get("params") or {}
    if kind == "echo":
        return literal(str(params.get("echo", "")))
    if kind == "date":
        fmt = no_braces(str(params.get("format", "%Y-%m-%d")), "a date format")
        offset = int(params.get("offset", 0))
        return f"{{now{f'{offset:+d}s' if offset else ''}:{fmt}}}"
    if kind == "clipboard":
        return "{clipboard}"
    if kind == "shell":
        return f"{{cmd:{no_braces(str(params.get('cmd', '')), 'the shell command')}}}"
    if kind == "script":
        return f"{{cmd:{no_braces(shlex.join(map(str, params.get('args', []))), 'the script')}}}"
    if kind == "choice":
        return f"{{choose:{var['name']}={option_list(params.get('values', []))}}}"
    if kind == "match":
        return f"{{abbr:{key_for(str(params.get('trigger', '')))}}}"
    if kind == "form":
        fields = params.get("fields", {})
        names = re.findall(r"\[\[\s*([\w.-]+)\s*\]\]", str(params.get("layout", "")))
        return {n: field(n, fields.get(n)) for n in names}
    raise Skip(f"variables of type “{kind}” aren't supported")


def convert(match: dict) -> tuple[str, dict]:
    """(key, abbreviation table) for one espanso match. Raises Skip."""
    triggers = match.get("triggers") or [match.get("trigger")]
    if not triggers or not triggers[0]:
        raise Skip("regex triggers aren't supported" if "regex" in match else "no trigger")
    key = key_for(str(triggers[0]))
    abbr = {}
    if "form" in match:
        text = form_text(str(match["form"]), match.get("form_fields", {}))
    else:
        for fmt in ("replace", "markdown", "html"):
            if fmt in match:
                body = str(match[fmt])
                if fmt != "replace":
                    abbr["format"] = fmt
                break
        else:
            raise Skip("images aren't supported" if "image_path" in match else "nothing to insert")
        values = {}
        for var in match.get("vars", []):
            values[var["name"]] = variable(var)

        def ref(m: re.Match) -> str:
            name, _, part = m[1].partition(".")
            if name not in values:
                raise Skip(f"unknown variable “{{{{{m[1]}}}}}” (global variables aren't imported)")
            value = values[name]
            if isinstance(value, dict):
                if part not in value:
                    raise Skip(f"unknown form field “{m[1]}”")
                return value[part]
            return value

        out, pos = [], 0
        for m in re.finditer(r"\{\{\s*([\w.-]+)\s*\}\}|\$\|\$", body):
            out.append(literal(body[pos:m.start()]))
            out.append("{cursor}" if m[0] == "$|$" else ref(m))
            pos = m.end()
        text = "".join(out) + literal(body[pos:])
    abbr["text"] = text
    abbr["description"] = str(match.get("label") or f"espanso {triggers[0]}")
    return key, abbr


def to_toml(abbrs: dict[str, dict]) -> str:
    out = []
    for key, abbr in abbrs.items():
        out += ["", f"[abbr.{k.toml_key(key)}]"]
        out += [f"{name} = {k.toml_str(value)}" for name, value in abbr.items()]
    return "\n".join(out) + "\n"


def import_espanso(path: Path, existing: set[str]) -> tuple[dict[str, dict], list[str]]:
    """Converted abbreviations, and notes on what was skipped."""
    import yaml

    data = yaml.safe_load(path.read_text()) or {}
    abbrs, notes = {}, []
    if data.get("global_vars"):
        notes.append("global_vars are not imported; matches using them are skipped")
    for match in data.get("matches", []):
        trigger = match.get("trigger") or (match.get("triggers") or ["?"])[0]
        try:
            key, abbr = convert(match)
            if key.lower() in existing or key.lower() in {a.lower() for a in abbrs}:
                raise Skip(f"“{key}” already exists")
        except Skip as e:
            notes.append(f"skipped {trigger}: {e}")
            continue
        abbrs[key] = abbr
    return abbrs, notes


def main(path: str, write: bool) -> None:
    cfg = k.Config(k.CONFIG_FILE)
    cfg.reload_if_changed()
    try:
        abbrs, notes = import_espanso(Path(path), set(cfg.abbrs))
    except ImportError:
        sys.exit("reading espanso files needs PyYAML: sudo dnf install python3-pyyaml")
    except Exception as e:  # OSError, yaml.YAMLError, unexpected structure
        sys.exit(f"cannot read {path}: {e}")
    for note in notes:
        print(note, file=sys.stderr)
    toml = to_toml(abbrs)
    if not write:
        print(toml, end="")
        return
    if not abbrs:
        sys.exit("nothing to import")
    shutil.copy2(k.CONFIG_FILE, k.CONFIG_FILE.with_name(k.CONFIG_FILE.name + ".bak"))
    with k.CONFIG_FILE.open("a") as f:
        f.write(f"\n# imported from espanso: {Path(path).name}{toml}")
    print(f"added {len(abbrs)} abbreviations to {k.CONFIG_FILE} "
          f"(previous version: {k.CONFIG_FILE.name}.bak)", file=sys.stderr)
