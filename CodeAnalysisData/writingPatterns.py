
# ------------------------------------------------------------
# Writing Patterns
# ------------------------------------------------------------
# This script assumes the following folder structure:
#
# code_website/
#   CodeAnalysisData/
#       consultationPatterns.py   <-- this file
#   exampleDataFiles/
#       participant1.txt
#       participant2.txt
#       ...
# This file: Writes one CSV file with participant-level metrics to this folder:
#    CodeAnalysisData/writing_patterns_metrics.csv
# - If a file has no "editor", it will skip that file and report it.
#
# Main outputs:
# - writing pace:
#    * final_word_count
#    * total_words_added
#    * net_words_added
#    * words_added_per_minute
#    * net_words_per_minute
# - pauses:
#    * pause_count
#    * pause_frequency_per_minute
#    * mean_pause_duration_sec
#    * median_pause_duration_sec
#    * max_pause_duration_sec
# - bursts:
#    * burst_count
#    * mean_burst_duration_sec
#    * mean_burst_words_added
#    * max_burst_words_added

# Definitions used here:
# - Pause = gap of at least PAUSE_THRESHOLD_MS between consecutive editor snapshots.
#  - Burst = a sequence of consecutive editor snapshots separated by less than PAUSE_THRESHOLD_MS.
# ------------------------------------------------------------


from __future__ import annotations

import csv
import html
import json
import re
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# CONFIG YOU WILL EDIT
PAUSE_THRESHOLD_MS = 2000

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "exampleDataFiles"
OUTPUT_CSV = SCRIPT_DIR / "writing_patterns_metrics.csv"


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_input_files(folder: Path) -> Iterable[Path]:
    seen = set()
    for pattern in ("*.json", "*.txt"):
        for path in sorted(folder.glob(pattern)):
            if path not in seen:
                seen.add(path)
                yield path


def html_to_plain_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<br\s*/?>", " ", raw_html, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def word_count(text: str) -> int:
    # Simple and stable word definition for English-like text.
    return len(re.findall(r"\b\w+\b", text))


def safe_messages(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages = data.get("messages", [])
    return messages if isinstance(messages, list) else []


def safe_chat_events(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    chat_events = data.get("chatEvents", [])
    return chat_events if isinstance(chat_events, list) else []


def safe_editor(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    editor = data.get("editor", [])
    return editor if isinstance(editor, list) else []


def get_max_timestamp(data: Dict[str, Any]) -> Optional[int]:
    timestamps: List[int] = []

    for msg in safe_messages(data):
        ts = msg.get("timestamp")
        if isinstance(ts, (int, float)):
            timestamps.append(int(ts))

    for snap in safe_editor(data):
        ts = snap.get("t_ms")
        if isinstance(ts, (int, float)):
            timestamps.append(int(ts))

    for event in safe_chat_events(data):
        ts = event.get("t_ms")
        if isinstance(ts, (int, float)):
            timestamps.append(int(ts))

    button_pressed = data.get("ButtonPressed")
    if isinstance(button_pressed, (int, float)):
        timestamps.append(int(button_pressed))

    submit_clicks = data.get("TimeStampOfSubmitClicks", [])
    if isinstance(submit_clicks, list):
        for ts in submit_clicks:
            if isinstance(ts, (int, float)):
                timestamps.append(int(ts))

    return max(timestamps) if timestamps else None


def build_editor_series(editor_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []

    for row in editor_rows:
        ts = row.get("t_ms")
        text_html = row.get("text", "")
        if not isinstance(ts, (int, float)):
            continue
        plain = html_to_plain_text(text_html if isinstance(text_html, str) else "")
        cleaned.append(
            {
                "t_ms": int(ts),
                "plain_text": plain,
                "word_count": word_count(plain),
            }
        )

    cleaned.sort(key=lambda x: x["t_ms"])
    return cleaned


def compute_pause_metrics(series: List[Dict[str, Any]], session_duration_min: Optional[float]) -> Dict[str, Any]:
    if len(series) < 2:
        return {
            "pause_count": 0,
            "pause_frequency_per_minute": None,
            "mean_pause_duration_sec": None,
            "median_pause_duration_sec": None,
            "max_pause_duration_sec": None,
        }

    gaps = [series[i]["t_ms"] - series[i - 1]["t_ms"] for i in range(1, len(series))]
    pause_gaps = [gap for gap in gaps if gap >= PAUSE_THRESHOLD_MS]

    pause_count = len(pause_gaps)
    pause_frequency_per_minute = (
        pause_count / session_duration_min if session_duration_min and session_duration_min > 0 else None
    )

    return {
        "pause_count": pause_count,
        "pause_frequency_per_minute": round(pause_frequency_per_minute, 4) if pause_frequency_per_minute is not None else None,
        "mean_pause_duration_sec": round(statistics.mean(pause_gaps) / 1000, 4) if pause_gaps else None,
        "median_pause_duration_sec": round(statistics.median(pause_gaps) / 1000, 4) if pause_gaps else None,
        "max_pause_duration_sec": round(max(pause_gaps) / 1000, 4) if pause_gaps else None,
    }


def split_into_bursts(series: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    if not series:
        return []

    bursts: List[List[Dict[str, Any]]] = [[series[0]]]

    for i in range(1, len(series)):
        gap = series[i]["t_ms"] - series[i - 1]["t_ms"]
        if gap >= PAUSE_THRESHOLD_MS:
            bursts.append([series[i]])
        else:
            bursts[-1].append(series[i])

    return bursts


def words_added_within_burst(burst: List[Dict[str, Any]]) -> int:
    if len(burst) < 2:
        return 0

    total_added = 0
    for i in range(1, len(burst)):
        delta = burst[i]["word_count"] - burst[i - 1]["word_count"]
        if delta > 0:
            total_added += delta
    return total_added


def compute_burst_metrics(series: List[Dict[str, Any]]) -> Dict[str, Any]:
    bursts = split_into_bursts(series)
    if not bursts:
        return {
            "burst_count": 0,
            "mean_burst_duration_sec": None,
            "mean_burst_words_added": None,
            "max_burst_words_added": None,
        }

    burst_durations_ms: List[int] = []
    burst_words_added: List[int] = []

    for burst in bursts:
        duration = burst[-1]["t_ms"] - burst[0]["t_ms"]
        burst_durations_ms.append(duration)
        burst_words_added.append(words_added_within_burst(burst))

    return {
        "burst_count": len(bursts),
        "mean_burst_duration_sec": round(statistics.mean(burst_durations_ms) / 1000, 4) if burst_durations_ms else None,
        "mean_burst_words_added": round(statistics.mean(burst_words_added), 4) if burst_words_added else None,
        "max_burst_words_added": max(burst_words_added) if burst_words_added else None,
    }


def compute_writing_pace_metrics(series: List[Dict[str, Any]], session_duration_min: Optional[float]) -> Dict[str, Any]:
    if not series:
        return {
            "first_editor_t_ms": None,
            "last_editor_t_ms": None,
            "editor_duration_min": None,
            "initial_word_count": None,
            "final_word_count": None,
            "total_words_added": None,
            "net_words_added": None,
            "words_added_per_minute": None,
            "net_words_per_minute": None,
        }

    first_t = series[0]["t_ms"]
    last_t = series[-1]["t_ms"]
    editor_duration_min = (last_t - first_t) / 60000 if last_t > first_t else 0.0

    initial_word_count = series[0]["word_count"]
    final_word_count = series[-1]["word_count"]

    total_words_added = 0
    for i in range(1, len(series)):
        delta = series[i]["word_count"] - series[i - 1]["word_count"]
        if delta > 0:
            total_words_added += delta

    net_words_added = final_word_count - initial_word_count

    # Prefer editor duration when possible, otherwise fall back to session duration.
    denominator_min = editor_duration_min if editor_duration_min > 0 else session_duration_min

    words_added_per_minute = (
        total_words_added / denominator_min if denominator_min and denominator_min > 0 else None
    )
    net_words_per_minute = (
        net_words_added / denominator_min if denominator_min and denominator_min > 0 else None
    )

    return {
        "first_editor_t_ms": first_t,
        "last_editor_t_ms": last_t,
        "editor_duration_min": round(editor_duration_min, 4),
        "initial_word_count": initial_word_count,
        "final_word_count": final_word_count,
        "total_words_added": total_words_added,
        "net_words_added": net_words_added,
        "words_added_per_minute": round(words_added_per_minute, 4) if words_added_per_minute is not None else None,
        "net_words_per_minute": round(net_words_per_minute, 4) if net_words_per_minute is not None else None,
    }


def analyze_file(path: Path) -> Optional[Dict[str, Any]]:
    data = load_json(path)

    participant_id = data.get("id", path.stem)
    editor_rows = safe_editor(data)
    messages = safe_messages(data)
    chat_events = safe_chat_events(data)

    if not editor_rows:
        print(f"Skipping {path.name}: no editor data found.")
        return None

    series = build_editor_series(editor_rows)
    if not series:
        print(f"Skipping {path.name}: editor data exists but could not be parsed.")
        return None

    session_end_ms = get_max_timestamp(data)
    session_duration_min = (session_end_ms / 60000) if session_end_ms and session_end_ms > 0 else None

    result: Dict[str, Any] = {
        "source_file": path.name,
        "participant_id": participant_id,
        "has_messages": bool(messages),
        "has_chat_events": bool(chat_events),
        "message_count": len(messages),
        "chat_event_count": len(chat_events),
        "session_end_ms": session_end_ms,
        "session_duration_min": round(session_duration_min, 4) if session_duration_min is not None else None,
        "pause_threshold_ms": PAUSE_THRESHOLD_MS,
    }

    result.update(compute_writing_pace_metrics(series, session_duration_min))
    result.update(compute_pause_metrics(series, session_duration_min))
    result.update(compute_burst_metrics(series))

    return result


def write_csv(rows: List[Dict[str, Any]], output_path: Path) -> None:
    if not rows:
        print("No results to save.")
        return

    fieldnames = list(rows[0].keys())
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    if not DATA_DIR.exists():
        print(f"Data folder not found: {DATA_DIR}")
        print("Create the folder and put your example/session files there.")
        return

    files = list(iter_input_files(DATA_DIR))
    if not files:
        print(f"No .json or .txt files found in: {DATA_DIR}")
        return

    all_rows: List[Dict[str, Any]] = []

    print(f"Reading files from: {DATA_DIR}")
    print(f"Pause threshold: {PAUSE_THRESHOLD_MS} ms")
    print("-" * 72)

    for path in files:
        result = analyze_file(path)
        if result is not None:
            all_rows.append(result)
            print(
                f"{path.name}: "
                f"final_words={result['final_word_count']}, "
                f"words_added_per_min={result['words_added_per_minute']}, "
                f"pause_count={result['pause_count']}, "
                f"burst_count={result['burst_count']}"
            )

    print("-" * 72)
    write_csv(all_rows, OUTPUT_CSV)
    print(f"Saved output to: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
