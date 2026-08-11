"""Mirror every source file's comments into a comment-only tree.

WHY
    server.js/agent.py accumulated years of dated investigation comments
    ("2026-07-24 실측...", "2026-08-11 FileZilla 체크박스 실측...") that make
    the files harder to scan for actual logic. This tool copies just the
    comments into a sibling directory (../code-generator-comment/) with the
    SAME relative path (plus a .md extension), so debugging can jump to the
    identical path in either tree.

MEMORY: streams one file at a time — never holds more than one source
    file's text and its extracted comments in memory. Peak memory is
    O(largest single file), not O(whole codebase).
TIME: O(total source bytes) — one linear scan per file (a hand-rolled
    string/comment state machine, not a backtracking regex), then a second
    O(comments in that file) pass to merge adjacent line-comments into
    blocks. No file is read or scanned twice.

USAGE
    python tools/extract_comments.py
    python tools/extract_comments.py --dry-run   # print what would be written, write nothing
"""
import argparse
import os
import sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(os.path.dirname(SRC_ROOT), "code-generator-comment")

INCLUDE_TOP_DIRS = {"server", "agent", "poc", "ui"}
EXCLUDE_DIR_NAMES = {
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    "generated-wdio", "recorded-events", "dist", "build", ".next", "golden",
}
EXT_LANG = {".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js", ".py": "py"}


def extract_js_comments(text):
    """One pass over JS/JSX source, tracking whether we're inside a string
    (', ", `) so a quote or slash inside a string is never mistaken for a
    comment delimiter. Returns [(start_line, comment_text), ...]."""
    out = []
    i, n = 0, len(text)
    line = 1
    in_str = None
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == in_str:
                in_str = None
            elif c == "\n":
                line += 1
            i += 1
            continue
        if c in "\"'`":
            in_str = c
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            if j == -1:
                j = n
            out.append((line, text[i + 2:j].strip()))
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            end = j if j != -1 else n
            content = text[i + 2:end]
            out.append((line, content.strip()))
            line += content.count("\n")
            i = end + 2 if j != -1 else n
            continue
        if c == "\n":
            line += 1
        i += 1
    return out


def extract_py_comments(text):
    """Same idea for Python: '#' line comments plus triple-quoted blocks
    (treated as comments — in this codebase they're used as docstring-style
    narrative, not as data). Single-quoted/double-quoted strings are
    tracked and skipped so a '#' inside a string literal is never captured."""
    out = []
    i, n = 0, len(text)
    line = 1
    in_str = None  # the exact quote sequence closing the current string
    while i < n:
        c = text[i]
        if in_str:
            if text.startswith(in_str, i):
                i += len(in_str)
                in_str = None
                continue
            if c == "\\" and len(in_str) == 1 and i + 1 < n:
                i += 2
                continue
            if c == "\n":
                line += 1
            i += 1
            continue
        if text.startswith('"""', i) or text.startswith("'''", i):
            q = text[i:i + 3]
            j = text.find(q, i + 3)
            end = j if j != -1 else n
            content = text[i + 3:end]
            out.append((line, content.strip()))
            line += content.count("\n")
            i = end + 3 if j != -1 else n
            continue
        if c in "\"'":
            in_str = c
            i += 1
            continue
        if c == "#":
            j = text.find("\n", i)
            if j == -1:
                j = n
            out.append((line, text[i + 1:j].strip()))
            i = j
            continue
        if c == "\n":
            line += 1
        i += 1
    return out


def merge_adjacent(comments):
    """Consecutive single-line comments (##, //) one source line apart get
    folded into one block — most of this codebase's real comments are many
    consecutive // or # lines forming one paragraph, and reading them as N
    separate one-liners in the output would be far less useful than reading
    them as the block they actually are."""
    if not comments:
        return []
    merged = []
    cur_start, cur_lines, last_line = comments[0][0], [comments[0][1]], comments[0][0]
    for start, text in comments[1:]:
        if start == last_line + 1 and "\n" not in text and "\n" not in cur_lines[-1]:
            cur_lines.append(text)
        else:
            merged.append((cur_start, "\n".join(cur_lines)))
            cur_start, cur_lines = start, [text]
        last_line = start
    merged.append((cur_start, "\n".join(cur_lines)))
    return merged


def iter_source_files():
    for top in sorted(INCLUDE_TOP_DIRS):
        top_path = os.path.join(SRC_ROOT, top)
        if not os.path.isdir(top_path):
            continue
        for dirpath, dirnames, filenames in os.walk(top_path):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
            for fn in filenames:
                ext = os.path.splitext(fn)[1]
                if ext in EXT_LANG:
                    yield os.path.join(dirpath, fn), EXT_LANG[ext]


def render_markdown(rel_path, comments):
    lines = [f"# {rel_path.replace(os.sep, '/')}", ""]
    if not comments:
        lines.append("_(no comments found)_")
    for start, text in comments:
        lines.append(f"## L{start}")
        lines.append("")
        lines.append(text if text else "_(empty)_")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    written, skipped_empty, total_comments = 0, 0, 0
    for src_path, lang in iter_source_files():
        rel = os.path.relpath(src_path, SRC_ROOT)
        try:
            with open(src_path, encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, OSError) as e:
            print(f"  skip (read error): {rel}: {e}", file=sys.stderr)
            continue

        raw = extract_js_comments(text) if lang == "js" else extract_py_comments(text)
        comments = merge_adjacent(raw)
        total_comments += len(comments)
        if not comments:
            skipped_empty += 1
            continue

        out_path = os.path.join(OUT_ROOT, rel + ".md")
        if args.dry_run:
            print(f"  would write {len(comments)} block(s) -> {out_path}")
            written += 1
            continue
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(render_markdown(rel, comments))
        written += 1

    print(f"\n{written} file(s) written, {skipped_empty} file(s) had no comments, "
          f"{total_comments} comment block(s) total.")
    if not args.dry_run:
        print(f"output root: {OUT_ROOT}")


if __name__ == "__main__":
    main()
