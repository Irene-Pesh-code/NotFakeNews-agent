
import argparse
import json
import re
from pathlib import Path


def safe_filename(value: str) -> str:
    """Создаёт безопасное имя файла."""
    value = re.sub(r'[\\/:*?"<>|#^\[\]]', "_", str(value))
    return value.strip(" .")[:100] or "untitled"


def md_escape(value) -> str:
    """Минимальное экранирование для Markdown."""
    text = str(value or "")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace(
        "\n", " "
    )


def yaml_string(value) -> str:
    """Строка, совместимая с YAML."""
    return json.dumps(str(value or ""), ensure_ascii=False)


def describe_edge(edge: dict) -> str:
    """Объясняет тип связи между источниками."""
    descriptions = {
        "link": "Ссылка между страницами; распространение не доказано",
        "citation": "Цитирование",
        "republication": "Перепубликация",
        "probable_republication": "Предполагаемая перепубликация",
        "attribution": "Атрибуция источника",
    }
    return descriptions.get(
        str(edge.get("kind") or "").lower(),
        "Тип связи не классифицирован"
    )


def format_date(node: dict) -> str:
    return str(node.get("published_at") or "Дата неизвестна")


def compare_claims(nodes: dict, claims_by_id: dict) -> list[str]:
    """
    Сравнивает результаты проверки утверждений между источниками.
    Различие статусов само по себе не доказывает изменение факта.
    """
    lines = [
        "## Изменение фактического содержания",
        "",
        "> Предварительное сравнение: различие результатов проверки "
        "не доказывает, что источник изменил факт.",
        "",
    ]

    claim_ids = sorted({
        str(claim_id)
        for node in nodes.values()
        for claim_id in (node.get("matches") or {})
    })

    if not claim_ids:
        lines.append("Нет данных для сравнения утверждений.")
        return lines

    status_names = {
        "exact": "Дословное совпадение",
        "fuzzy": "Приблизительное совпадение",
        "facts": "Совпадение ключевых фактов",
        "not_found": "Совпадение не найдено",
        "unknown": "Статус неизвестен",
    }

    for claim_id in claim_ids:
        claim = claims_by_id.get(claim_id, {})
        claim_text = claim.get("text") or claim_id

        lines.extend([
            f"### {md_escape(claim_text)}",
            "",
        ])

        observations = []

        for node_id, node in nodes.items():
            matches = node.get("matches") or {}
            match = matches.get(claim_id)

            if not isinstance(match, dict):
                continue

            observations.append({
                "node_id": node_id,
                "node": node,
                "status": str(match.get("status") or "unknown"),
                "fragment": match.get("fragment"),
            })

        observations.sort(
            key=lambda item: (
                not bool(item["node"].get("published_at")),
                str(item["node"].get("published_at") or ""),
            )
        )

        if not observations:
            lines.extend(["Нет результатов проверки.", ""])
            continue

        for item in observations:
            node = item["node"]
            title = node.get("title") or item["node_id"]
            status = item["status"]
            status_label = status_names.get(status, status)

            lines.append(
                f"- **{format_date(node)} — {md_escape(title)}** "
                f"({item['node_id']}): {md_escape(status_label)}"
            )

            if item["fragment"]:
                lines.append(
                    f"  - Фрагмент: {md_escape(item['fragment'])}"
                )

        statuses = {item["status"] for item in observations}

        if len(statuses) > 1:
            lines.append(
                "- **Наблюдение:** статусы различаются. "
                "Нужно проверить тексты источников вручную."
            )
        else:
            lines.append(
                "- **Наблюдение:** статусы совпадают "
                "во всех источниках с результатами проверки."
            )

        lines.append("")

    return lines


def export_graph(graph_path: Path, vault_path: Path) -> Path:
    """Экспортирует JSON-граф в заметки Obsidian."""
    data = json.loads(graph_path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise ValueError("Корень JSON должен быть объектом")

    nodes_list = data.get("nodes", [])
    edges = data.get("edges", [])
    claims = data.get("claims", [])

    if not isinstance(nodes_list, list):
        raise ValueError("Поле nodes должно быть списком")

    if not isinstance(edges, list):
        raise ValueError("Поле edges должно быть списком")

    if not isinstance(claims, list):
        claims = []

    by_id = {}

    for node in nodes_list:
        if isinstance(node, dict) and node.get("id") is not None:
            by_id[str(node["id"])] = node

    if not by_id:
        raise ValueError("Граф не содержит корректных узлов")

    run_id = graph_path.stem
    out_dir = vault_path / "NotFakeNews" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    result = data.get("result") or {}
    if not isinstance(result, dict):
        result = {}

    origin_url = result.get("origin_url")

    origin_id = next(
        (
            node_id
            for node_id, node in by_id.items()
            if origin_url and node.get("url") == origin_url
        ),
        None,
    )

    if origin_id is None:
        candidate = data.get("origin")

        if isinstance(candidate, dict):
            candidate = candidate.get("id")

        if candidate is not None and str(candidate) in by_id:
            origin_id = str(candidate)

    input_id = next(
        (
            node_id
            for node_id, node in by_id.items()
            if node.get("found_via") == "input"
        ),
        None,
    )

    filenames = {
        node_id: (
            f"{safe_filename(run_id)}_{safe_filename(node_id)}"
        )
        for node_id in by_id
    }

    def link(node_id: str) -> str:
        return f"[[{filenames[node_id]}]]"

    claims_by_id = {
        str(claim["id"]): claim
        for claim in claims
        if isinstance(claim, dict) and claim.get("id") is not None
    }

    incoming = {node_id: [] for node_id in by_id}
    outgoing = {node_id: [] for node_id in by_id}
    valid_edges = []

    for edge in edges:
        if not isinstance(edge, dict):
            continue

        src = str(edge.get("src", ""))
        dst = str(edge.get("dst", ""))

        if src not in by_id or dst not in by_id:
            continue

        outgoing[src].append(edge)
        incoming[dst].append(edge)
        valid_edges.append(edge)

    # Создание отдельной заметки для каждого источника.
    for node_id, node in by_id.items():
        title = node.get("title") or node_id
        url = node.get("url")
        matches = node.get("matches") or {}
        attributions = node.get("attributions") or []

        if not isinstance(matches, dict):
            matches = {}

        if not isinstance(attributions, list):
            attributions = []

        is_origin = node_id == origin_id
        is_input = node_id == input_id

        lines = [
            "---",
            f"id: {yaml_string(node_id)}",
            f"title: {yaml_string(title)}",
            f"run_id: {yaml_string(run_id)}",
            f"published_at: {yaml_string(node.get('published_at') or '')}",
            f"date_has_time: {str(bool(node.get('date_has_time'))).lower()}",
            f"date_source: {yaml_string(node.get('date_source') or '')}",
            f"found_via: {yaml_string(node.get('found_via') or '')}",
            f"verified: {str(bool(node.get('verified'))).lower()}",
            f"is_origin: {str(is_origin).lower()}",
            f"is_input_source: {str(is_input).lower()}",
            "---",
            "",
            f"# {md_escape(title)}",
            "",
            f"- **URL:** {url or 'Не указан'}",
            f"- **Дата публикации:** {format_date(node)}",
            f"- **Источник даты:** {node.get('date_source') or 'Неизвестен'}",
            f"- **Способ обнаружения:** {node.get('found_via') or 'Неизвестен'}",
            f"- **Уровень совпадения:** {node.get('strength', 0)}",
            f"- **Подтверждение узла:** {'Есть' if node.get('verified') else 'Нет'}",
            f"- **Начальная публикация анализа:** {'Да' if is_input else 'Нет'}",
            f"- **Первоисточник новости:** "
            f"{'Установлен' if is_origin else 'Не установлен' if origin_id is None else 'Нет'}",
            "",
            "## Проверяемые утверждения",
            "",
        ]

        if not matches:
            lines.append("Данные о совпадениях отсутствуют.")
        else:
            for claim_id, match in matches.items():
                if not isinstance(match, dict):
                    continue

                claim = claims_by_id.get(str(claim_id), {})

                lines.extend([
                    f"### {md_escape(claim.get('text') or claim_id)}",
                    f"- **Тип утверждения:** {md_escape(claim.get('kind') or 'Неизвестен')}",
                    f"- **Статус совпадения:** {md_escape(match.get('status') or 'unknown')}",
                    f"- **Оценка совпадения:** {match.get('score', 'Не указана')}",
                    f"- **Найденный фрагмент:** {md_escape(match.get('fragment') or 'Не найден')}",
                    "",
                ])

        # Связи между публикациями.
        lines.extend(["## Связи публикации", ""])

        if outgoing[node_id]:
            lines.append("### Ссылается на")

            for edge in outgoing[node_id]:
                dst = str(edge["dst"])
                description = describe_edge(edge)
                original_label = edge.get("label")

                line = f"- {link(dst)} — {md_escape(description)}"

                if original_label:
                    line += f" (метка: {md_escape(original_label)})"

                lines.append(line)

            lines.append("")

        if incoming[node_id]:
            lines.append("### На эту публикацию ссылаются")

            for edge in incoming[node_id]:
                src = str(edge["src"])
                description = describe_edge(edge)
                original_label = edge.get("label")

                line = f"- {link(src)} — {md_escape(description)}"

                if original_label:
                    line += f" (метка: {md_escape(original_label)})"

                lines.append(line)

            lines.append("")

        # Атрибуции — упоминания других источников.
        lines.extend(["## Атрибуции", ""])
        found_attribution = False

        for attribution in attributions:
            if not isinstance(attribution, dict):
                continue

            label = md_escape(
                attribution.get("label") or "Без названия"
            )
            attribution_url = attribution.get("url")
            followed = attribution.get("followed_node")

            lines.append(f"- **Упоминание:** {label}")

            if attribution_url:
                lines.append(
                    f"  - URL источника: {attribution_url}"
                )

            if followed is not None and str(followed) in by_id:
                lines.append(
                    f"  - Найденный узел: {link(str(followed))}"
                )
            else:
                lines.append(
                    "  - Связь с узлом графа не установлена"
                )

            if attribution.get("follow_error"):
                lines.append(
                    f"  - Причина ошибки: "
                    f"{md_escape(attribution['follow_error'])}"
                )

            found_attribution = True

        if not found_attribution:
            lines.append("Атрибуции отсутствуют.")

        # Сохраняем исходный текст, если он есть в JSON.
        lines.extend(["", "## Исходный текст", ""])
        source_text = node.get("text")

        if source_text:
            lines.append(str(source_text))
        else:
            lines.append(
                "> Исходный текст публикации не сохранён в JSON."
            )

        note_path = out_dir / f"{filenames[node_id]}.md"
        note_path.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    # Главная заметка проверки.
    index_lines = [
        f"# Проверка новости: {run_id}",
        "",
        f"- **Дата анализа:** {data.get('created_at') or 'Неизвестна'}",
        f"- **Уверенность:** {result.get('confidence') or 'Не определена'}",
        f"- **Причина вывода:** {md_escape(result.get('reason') or 'Не указана')}",
        f"- **Начальная публикация:** {link(input_id) if input_id else 'Не установлена'}",
        f"- **Первоисточник:** {link(origin_id) if origin_id else 'Не установлен'}",
        f"- **Количество узлов:** {len(by_id)}",
        f"- **Количество связей:** {len(valid_edges)}",
        "",
        "## Источники и хронология",
        "",
    ]

    dated_nodes = sorted(
        by_id.items(),
        key=lambda item: (
            not bool(item[1].get("published_at")),
            str(item[1].get("published_at") or ""),
        ),
    )

    for node_id, node in dated_nodes:
        tags = []

        if node_id == input_id:
            tags.append("начальная публикация")

        if node_id == origin_id:
            tags.append("первоисточник")

        suffix = f" — {', '.join(tags)}" if tags else ""

        index_lines.append(
            f"- {format_date(node)} — {link(node_id)} — "
            f"{md_escape(node.get('title') or node_id)}{suffix}"
        )

    # Общий список связей.
    index_lines.extend(["", "## Граф связей", ""])

    if not valid_edges:
        index_lines.append("Связи между источниками отсутствуют.")

    for edge in valid_edges:
        src = str(edge["src"])
        dst = str(edge["dst"])
        label = describe_edge(edge)
        original_label = edge.get("label")

        line = f"- {link(src)} → {link(dst)} — {md_escape(label)}"

        if original_label:
            line += f" (метка: {md_escape(original_label)})"

        index_lines.append(line)

    # Сравнение содержания утверждений между источниками.
    index_lines.extend(["", ""])
    index_lines.extend(compare_claims(by_id, claims_by_id))

    index_path = out_dir / "Итог проверки.md"
    index_path.write_text(
        "\n".join(index_lines) + "\n",
        encoding="utf-8",
    )

    return index_path


def main():
    parser = argparse.ArgumentParser(
        description="Экспорт графа NotFakeNews-agent в Obsidian"
    )

    parser.add_argument(
        "graph",
        type=Path,
        help="Путь к JSON графа",
    )

    parser.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="Путь к хранилищу Obsidian",
    )

    args = parser.parse_args()

    if not args.graph.is_file():
        parser.error(f"Файл графа не найден: {args.graph}")

    if not args.vault.is_dir():
        parser.error(
            "Хранилище Obsidian не найдено. "
            "Сначала создай его в Obsidian."
        )

    try:
        result = export_graph(args.graph, args.vault)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        parser.error(f"Не удалось экспортировать граф: {exc}")

    print(f"Экспорт завершён: {result}")


if __name__ == "__main__":
    main()