import io
from typing import List

from lib.generate.methods import escape_html_preserve_code_blocks, format_type, table_cell
from lib.ptypes import PropertyInfo


def generate_props(props: List[PropertyInfo], output: io.TextIOWrapper):
    if not props:
        return

    output.write("| Property | Type | Description |\n")
    output.write("|-|-|-|\n")

    for prop in props:
        propType = format_type(prop.get("type") or "", escape_or=True)
        docs = prop["doc"] if "doc" in prop else ""
        # Clean up the doc string - replace newlines with spaces and escape markdown table characters and HTML
        if docs:
            docs_cell = table_cell(escape_html_preserve_code_blocks(docs))
        else:
            docs_cell = ""
        output.write(f"| `{prop['name']}` | {propType} | {docs_cell} |\n")

    output.write("\n")
