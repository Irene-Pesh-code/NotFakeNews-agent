import argparse
import html
import json
from pathlib import Path

from pyvis.network import Network


def load_graph(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data.get("nodes"), list):
        raise ValueError("В JSON отсутствует список nodes")

    if not isinstance(data.get("edges"), list):
        raise ValueError("В JSON отсутствует список edges")

    return data


def build_html(data: dict, output: Path) -> None:
    net = Network(
        height="800px",
        width="100%",
        directed=True,
        bgcolor="#111827",
        font_color="#f9fafb",
        cdn_resources="in_line",
    )

    nodes = {
        node["id"]: node
        for node in data["nodes"]
    }

    origin_url = (
        data.get("result", {}).get("origin_url")
    )

    origin_id = next(
        (
            node["id"]
            for node in data["nodes"]
            if node.get("url") == origin_url
        ),
        None,
    )

    for node in data["nodes"]:
        node_id = node["id"]
        title = html.escape(
            node.get("title") or node_id
        )
        url = node.get("url") or ""
        date = node.get("published_at") or "Дата неизвестна"
        verified = node.get("verified", False)

        details = (
            f"<b>{title}</b><br>"
            f"Дата: {html.escape(str(date))}<br>"
            f"Проверка: {'подтверждено' if verified else 'не подтверждено'}"
        )

        if url:
            safe_url = html.escape(url, quote=True)
            details += (
                f'<br><a href="{safe_url}" '
                f'target="_blank" rel="noopener noreferrer">'
                "Открыть источник</a>"
            )

        color = (
            "#22c55e" if node_id == origin_id
            else "#60a5fa" if verified
            else "#f59e0b"
        )

        net.add_node(
            node_id,
            label=title[:45],
            title=details,
            color=color,
            shape="dot",
            size=25 if node_id == origin_id else 16,
        )

    for edge in data["edges"]:
        src = edge.get("src")
        dst = edge.get("dst")

        if src not in nodes or dst not in nodes:
            continue

        net.add_edge(
            src,
            dst,
            label=edge.get("label") or edge.get("kind", ""),
            title=edge.get("kind", "Связь"),
            arrows="to",
        )

    net.set_options("""
    {
      "physics": {
        "enabled": true,
        "stabilization": {"iterations": 150}
      },
      "interaction": {
        "hover": true,
        "navigationButtons": true,
        "keyboard": true
      },
      "edges": {
        "smooth": {"type": "dynamic"},
        "font": {"size": 11, "align": "middle"}
      }
    }
    """)

    output.parent.mkdir(parents=True, exist_ok=True)
    net.write_html(str(output), notebook=False, open_browser=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("graph", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/rendered/graph.html"),
    )
    args = parser.parse_args()

    data = load_graph(args.graph)
    build_html(data, args.output)
    print(f"Граф сохранён: {args.output.resolve()}")


if __name__ == "__main__":
    main()
