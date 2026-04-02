from __future__ import annotations

import csv
import json
import re
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ------------------------------------------------------------
# Consultation frequency and timing analysis
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
#
# It creates:
#   1. consultation_summary_metrics.csv
#   2. consultation_event_metrics.csv
#
# The script is robust to missing "messages", "chatEvents", or "editor".
# If a field is missing, the script records empty / None-based results
# instead of crashing.
# ------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "exampleDataFiles"

SUMMARY_OUTPUT_CSV = SCRIPT_DIR / "consultation_summary_metrics.csv"
EVENT_OUTPUT_CSV = SCRIPT_DIR / "consultation_event_metrics.csv"


def strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def count_words(text: str) -> int:
    if not text:
        return 0
    return len(re.findall(r"\b\w+\b", text))


def safe_load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Skipping {path.name}: could not read JSON ({e})")
        return None


def get_editor_snapshots(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshots = data.get("editor", [])
    if not isinstance(snapshots, list):
        return []

    cleaned = []
    for item in snapshots:
        if not isinstance(item, dict):
            continue
        t_ms = item.get("t_ms")
        text = item.get("text", "")
        if isinstance(t_ms, (int, float)):
            plain_text = strip_html(str(text))
            cleaned.append({
                "t_ms": int(t_ms),
                "text": plain_text,
                "word_count": count_words(plain_text),
            })
    cleaned.sort(key=lambda x: x["t_ms"])
    return cleaned


def get_messages(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        return []

    cleaned = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        timestamp = item.get("timestamp")
        sender = item.get("sender")
        text = item.get("text", "")
        if isinstance(timestamp, (int, float)) and isinstance(sender, str):
            cleaned.append({
                "timestamp": int(timestamp),
                "sender": sender,
                "text": str(text),
            })
    cleaned.sort(key=lambda x: x["timestamp"])
    return cleaned


def get_chat_events(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = data.get("chatEvents", [])
    if not isinstance(events, list):
        return []

    cleaned = []
    for item in events:
        if not isinstance(item, dict):
            continue
        t_ms = item.get("t_ms")
        event_type = item.get("type")
        if isinstance(t_ms, (int, float)) and isinstance(event_type, str):
            cleaned.append({
                "t_ms": int(t_ms),
                "type": event_type,
                "source": item.get("source"),
            })
    cleaned.sort(key=lambda x: x["t_ms"])
    return cleaned


def get_session_end_ms(
    editor: List[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    chat_events: List[Dict[str, Any]],
    submit_clicks: List[Any],
) -> Optional[int]:
    timestamps: List[int] = []

    timestamps.extend([x["t_ms"] for x in editor])
    timestamps.extend([x["timestamp"] for x in messages])
    timestamps.extend([x["t_ms"] for x in chat_events])

    for ts in submit_clicks:
        if isinstance(ts, (int, float)):
            timestamps.append(int(ts))

    return max(timestamps) if timestamps else None


def get_first_editor_time(editor: List[Dict[str, Any]]) -> Optional[int]:
    return editor[0]["t_ms"] if editor else None


def get_first_chat_open_time(data: Dict[str, Any], chat_events: List[Dict[str, Any]]) -> Optional[int]:
    candidates: List[int] = []

    button_pressed = data.get("ButtonPressed")
    if isinstance(button_pressed, (int, float)):
        candidates.append(int(button_pressed))

    for event in chat_events:
        if event["type"] in {"chat_open", "chat_expand"}:
            candidates.append(event["t_ms"])

    return min(candidates) if candidates else None


def classify_relative_stage(relative_position: float) -> str:
    if relative_position < 1 / 3:
        return "early"
    if relative_position < 2 / 3:
        return "middle"
    return "late"


def get_latest_editor_snapshot_before(
    consultation_time: int, editor: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    latest = None
    for snap in editor:
        if snap["t_ms"] <= consultation_time:
            latest = snap
        else:
            break
    return latest


def get_final_word_count(editor: List[Dict[str, Any]]) -> int:
    if not editor:
        return 0
    return editor[-1]["word_count"]


def compute_user_consultations(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [m for m in messages if m["sender"].lower() == "user"]


def write_csv(rows: List[Dict[str, Any]], output_path: Path | str) -> None:
    if not rows:
        print(f"No rows to save for {output_path}.")
        return

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(rows[0].keys())
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def analyze_file(path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    data = safe_load_json(path)
    if data is None:
        return {}, []

    editor = get_editor_snapshots(data)
    messages = get_messages(data)
    chat_events = get_chat_events(data)
    submit_clicks = data.get("TimeStampOfSubmitClicks", [])
    if not isinstance(submit_clicks, list):
        submit_clicks = []

    participant_id = str(data.get("id", path.stem))
    consultations = compute_user_consultations(messages)
    session_end_ms = get_session_end_ms(editor, messages, chat_events, submit_clicks)
    first_editor_ms = get_first_editor_time(editor)
    first_chat_open_ms = get_first_chat_open_time(data, chat_events)
    final_word_count = get_final_word_count(editor)

    total_consultations = len(consultations)
    session_duration_min = (
        session_end_ms / 60000 if isinstance(session_end_ms, int) and session_end_ms > 0 else None
    )
    consultations_per_min = (
        total_consultations / session_duration_min
        if session_duration_min not in (None, 0)
        else None
    )

    early_count = 0
    middle_count = 0
    late_count = 0

    event_rows: List[Dict[str, Any]] = []

    for i, consultation in enumerate(consultations, start=1):
        consultation_ms = consultation["timestamp"]

        relative_task_position = (
            consultation_ms / session_end_ms
            if isinstance(session_end_ms, int) and session_end_ms > 0
            else None
        )
        task_stage = (
            classify_relative_stage(relative_task_position)
            if relative_task_position is not None
            else None
        )

        if task_stage == "early":
            early_count += 1
        elif task_stage == "middle":
            middle_count += 1
        elif task_stage == "late":
            late_count += 1

        latest_snapshot = get_latest_editor_snapshot_before(consultation_ms, editor)
        words_written_so_far = latest_snapshot["word_count"] if latest_snapshot else 0
        proportion_of_final_text_written = (
            words_written_so_far / final_word_count
            if final_word_count > 0
            else None
        )

        time_since_first_edit_sec = (
            (consultation_ms - first_editor_ms) / 1000
            if first_editor_ms is not None
            else None
        )
        time_since_first_chat_open_sec = (
            (consultation_ms - first_chat_open_ms) / 1000
            if first_chat_open_ms is not None
            else None
        )

        event_rows.append({
            "participant_id": participant_id,
            "source_file": path.name,
            "consultation_number": i,
            "consultation_timestamp_ms": consultation_ms,
            "consultation_timestamp_sec": round(consultation_ms / 1000, 3),
            "user_message_text": consultation["text"],
            "task_stage": task_stage,
            "relative_task_position": round(relative_task_position, 4) if relative_task_position is not None else None,
            "words_written_so_far": words_written_so_far,
            "final_word_count": final_word_count,
            "proportion_of_final_text_written": round(proportion_of_final_text_written, 4) if proportion_of_final_text_written is not None else None,
            "time_since_first_edit_sec": round(time_since_first_edit_sec, 3) if time_since_first_edit_sec is not None else None,
            "time_since_first_chat_open_sec": round(time_since_first_chat_open_sec, 3) if time_since_first_chat_open_sec is not None else None,
        })

    first_consultation_ms = consultations[0]["timestamp"] if consultations else None
    first_consultation_sec = (
        round(first_consultation_ms / 1000, 3) if first_consultation_ms is not None else None
    )
    time_to_first_consultation_from_first_edit_sec = (
        (first_consultation_ms - first_editor_ms) / 1000
        if first_consultation_ms is not None and first_editor_ms is not None
        else None
    )
    time_to_first_consultation_from_chat_open_sec = (
        (first_consultation_ms - first_chat_open_ms) / 1000
        if first_consultation_ms is not None and first_chat_open_ms is not None
        else None
    )

    summary_row = {
        "participant_id": participant_id,
        "source_file": path.name,
        "session_duration_min": round(session_duration_min, 4) if session_duration_min is not None else None,
        "total_consultations": total_consultations,
        "first_consultation_sec": first_consultation_sec,
        "consultations_per_minute": round(consultations_per_min, 4) if consultations_per_min is not None else None,
        "early_consultations": early_count,
        "middle_consultations": middle_count,
        "late_consultations": late_count,
        "time_to_first_consultation_from_first_edit_sec": round(time_to_first_consultation_from_first_edit_sec, 3) if time_to_first_consultation_from_first_edit_sec is not None else None,
        "time_to_first_consultation_from_chat_open_sec": round(time_to_first_consultation_from_chat_open_sec, 3) if time_to_first_consultation_from_chat_open_sec is not None else None,
        "has_messages_field": "messages" in data,
        "has_chatEvents_field": "chatEvents" in data,
        "has_editor_field": "editor" in data,
    }

    return summary_row, event_rows


def get_data_files(data_dir: Path) -> List[Path]:
    if not data_dir.exists():
        print(f"Data folder not found: {data_dir}")
        return []

    files: List[Path] = []
    for pattern in ("*.txt", "*.json"):
        files.extend(sorted(data_dir.glob(pattern)))
    return files


def main() -> None:
    files = get_data_files(DATA_DIR)

    if not files:
        print("No data files found.")
        print(f"Expected files inside: {DATA_DIR}")
        return

    all_summary_rows: List[Dict[str, Any]] = []
    all_event_rows: List[Dict[str, Any]] = []

    for path in files:
        summary_row, event_rows = analyze_file(path)
        if summary_row:
            all_summary_rows.append(summary_row)
        all_event_rows.extend(event_rows)

    write_csv(all_summary_rows, SUMMARY_OUTPUT_CSV)
    write_csv(all_event_rows, EVENT_OUTPUT_CSV)

    print("Consultation frequency and timing analysis completed.")
    print(f"Processed files: {len(files)}")
    print(f"Summary CSV saved to: {SUMMARY_OUTPUT_CSV}")
    print(f"Event-level CSV saved to: {EVENT_OUTPUT_CSV}")


if __name__ == "__main__":
    main()
