"""Comparação segura entre duas versões de um projeto."""

from __future__ import annotations

from dataclasses import dataclass

from ..services.project_service import Project


@dataclass(frozen=True)
class ProjectDiff:
    text_changed: bool
    headers_changed: bool
    dictionary_changed: bool
    decisions_added: int
    decisions_removed: int
    old_characters: int
    new_characters: int

    @property
    def changed(self) -> bool:
        return any(
            (
                self.text_changed,
                self.headers_changed,
                self.dictionary_changed,
                self.decisions_added,
                self.decisions_removed,
            )
        )


def compare_projects(old: Project, new: Project) -> ProjectDiff:
    old_decisions = {
        str(record.get("source_id", record.get("id", ""))): str(record.get("action", "pending"))
        for record in old.state.recognition_records
    }
    new_decisions = {
        str(record.get("source_id", record.get("id", ""))): str(record.get("action", "pending"))
        for record in new.state.recognition_records
    }
    added = sum(1 for key, value in new_decisions.items() if old_decisions.get(key) != value)
    removed = sum(1 for key in old_decisions if key not in new_decisions)
    return ProjectDiff(
        text_changed=old.raw_text != new.raw_text,
        headers_changed=old.header_template != new.header_template,
        dictionary_changed=old.dictionary.as_config() != new.dictionary.as_config(),
        decisions_added=added,
        decisions_removed=removed,
        old_characters=len(old.raw_text),
        new_characters=len(new.raw_text),
    )


def format_project_diff(diff: ProjectDiff) -> str:
    lines = [
        "Comparação de projetos",
        "",
        f"Texto: {'alterado' if diff.text_changed else 'igual'} ({diff.old_characters} → {diff.new_characters} caracteres)",
        f"Headers: {'alterados' if diff.headers_changed else 'iguais'}",
        f"Dicionário: {'alterado' if diff.dictionary_changed else 'igual'}",
        f"Decisões novas/alteradas: {diff.decisions_added}",
        f"Decisões removidas: {diff.decisions_removed}",
    ]
    return "\n".join(lines)
