"""Checar os dados onde entram: um campo ruim volta ao padrao *dele*, e so ele.

Os arquivos do programa (`.plez`, `app.db`, `engine.json`, snapshot de
recuperacao) sao fronteiras: podem vir de uma versao antiga, de outra versao,
de um disco que falhou ou de alguem que editou a mao. Antes cada carregador
fazia `int(payload.get(...))` e tinha um `except` largo que devolvia **tudo** no
padrao -- ou nao tinha, e o programa nao abria (S-05, S-06, S-07; R-103 do
docs/ROADMAP_V3.md).

Aqui ha uma regra so, guiada pelos tipos declarados no dataclass de destino:

- campo ausente: padrao, sem aviso (e assim que arquivo antigo abre);
- campo com tipo errado que da para converter sem perder sentido (`"14"` para
  int, `3.0` para int): convertido;
- campo que nao da: padrao **daquele campo** + um `LoadWarning`;
- lista/dicionario: os itens bons ficam, os ruins saem (+ aviso);
- chave desconhecida: ignorada (arquivo de versao mais nova continua abrindo);
- raiz que nao e objeto: todos os padroes + aviso.

Depois de passar por aqui, o resto do programa confia nos tipos.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from dataclasses import dataclass
from typing import Any


class ProjectFormatError(ValueError):
    """O arquivo nao e um projeto que esta versao saiba abrir."""


@dataclass(frozen=True)
class LoadWarning:
    source: str
    field: str
    value: str
    reason: str

    def __str__(self) -> str:
        return f"{self.source}: campo '{self.field}' ({self.value}) {self.reason}"


class _Invalid(Exception):
    pass


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _coerce(value: Any, annotation: Any, dropped: list[Any]) -> Any:
    """Converte `value` para `annotation`, ou levanta `_Invalid`.

    Itens descartados de listas/dicionarios vao para `dropped`.
    """
    origin = typing.get_origin(annotation)
    if annotation is Any or annotation is object:
        return value
    if origin in (typing.Union, types.UnionType):
        for option in typing.get_args(annotation):
            if option is type(None) and value is None:
                return None
            try:
                return _coerce(value, option, dropped)
            except _Invalid:
                continue
        raise _Invalid
    if origin is list:
        if not isinstance(value, list):
            raise _Invalid
        (item_type,) = typing.get_args(annotation) or (Any,)
        result = []
        for item in value:
            try:
                result.append(_coerce(item, item_type, dropped))
            except _Invalid:
                dropped.append(item)
        return result
    if origin is dict:
        if not isinstance(value, dict):
            raise _Invalid
        key_type, value_type = typing.get_args(annotation) or (Any, Any)
        mapping = {}
        for key, item in value.items():
            try:
                mapping[_coerce(key, key_type, dropped)] = _coerce(item, value_type, dropped)
            except _Invalid:
                dropped.append(key)
        return mapping
    if annotation is bool:
        if isinstance(value, bool):
            return value
        raise _Invalid
    if annotation is int:
        if isinstance(value, bool):
            raise _Invalid
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
        raise _Invalid
    if annotation is float:
        if isinstance(value, bool):
            raise _Invalid
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError as exc:
                raise _Invalid from exc
        raise _Invalid
    if annotation is str:
        if isinstance(value, str):
            return value
        raise _Invalid
    if dataclasses.is_dataclass(annotation) and isinstance(annotation, type):
        if not isinstance(value, dict):
            raise _Invalid
        instance, nested = from_payload(annotation, value, source="")
        dropped.extend(nested)
        return instance
    raise _Invalid


def from_payload[T](cls: type[T], payload: object, *, source: str) -> tuple[T, list[LoadWarning]]:
    """Monta `cls` a partir de um objeto JSON, campo a campo. Nunca levanta por causa do conteudo."""
    defaults = cls()
    if not isinstance(payload, dict):
        return defaults, [LoadWarning(source, "*", _short(payload), "não é um objeto; usados os padrões")]

    hints = typing.get_type_hints(cls)
    values: dict[str, Any] = {}
    warnings: list[LoadWarning] = []
    for spec in dataclasses.fields(cls):  # type: ignore[arg-type]
        if spec.name not in payload:
            continue
        raw = payload[spec.name]
        dropped: list[Any] = []
        try:
            values[spec.name] = _coerce(raw, hints[spec.name], dropped)
        except _Invalid:
            warnings.append(LoadWarning(source, spec.name, _short(raw), "inválido; usado o padrão"))
            continue
        if dropped:
            warnings.append(
                LoadWarning(source, spec.name, _short(raw), f"{len(dropped)} item(ns) inválido(s) descartado(s)")
            )
    return dataclasses.replace(defaults, **values), warnings  # type: ignore[type-var]
