
from pathlib import Path
import argparse

from renderxboxchat.render_conversation import load_all_messages, render_full_html

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render Xbox conversation JSON pages into a searchable HTML viewer."
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        type=Path,
        help="Directory containing conversation_..._page_XXX.json files.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("conversation.html"),
        help="Output HTML file path.",
    )
    parser.add_argument(
        "--self-xuid",
        default="2533274884028261",
        help="Your XUID (messages from this XUID are right-aligned).",
    )

    args = parser.parse_args()

    messages = load_all_messages(args.input_dir)
    html = render_full_html(messages, args.self_xuid)
    args.out.write_text(html, encoding="utf-8")
    print(f"Wrote {args.out} ({len(messages)} messages merged)")


if __name__ == "__main__":
    main()