from __future__ import annotations

import csv
import json
import re
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ------------------------------------------------------------
# Behavior after LLM consultation analysis
# ------------------------------------------------------------
# Folder structure assumed:
#
# code_website/
#   CodeAnalysisData/
#       revisionAfterConsultation_clean.py   <-- place this file here
#   exampleDataFiles/
#       participant1.txt
#       participant2.txt
#       ...
#
# Outputs:
#   1. revision_after_consultation_summary.csv
#   2. revision_after_consultation_events.csv
#
# The script is robust to missing "messages", "chatEvents", or "editor".
# If a field is missing, it records empty / None-based results rather than crashing.
# ------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "exampleDataFiles"

SUMMARY_OUTPUT_CSV = SCRIPT_DIR / "revision_after_consultation_summary.csv"
EVENT_OUTPUT_CSV = SCRIPT_DIR / "revision_after_consultation_events.csv"

USER_SENDER_VALUES = {"user"}
ASSISTANT_SENDER_VALUES = {"llmassistant", "assistant", "ai", "model"}

# CONFIG YOU WILL EDIT:
# Burst-analysis window length in milliseconds.
# Example: 30000 = 30 seconds, 60000 = 60 seconds.
PRE_WINDOW_MS = 30000
POST_WINDOW_MS = 30000


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

    cleaned: List[Dict[str, Any]] = []
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

    cleaned: List[Dict[str, Any]] = []
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


def normalize_sender(sender: str) -> str:
    return sender.strip().lower()


def get_consultation_episodes(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    episodes: List[Dict[str, Any]] = []

    i = 0
    while i < len(messages):
        msg = messages[i]
        sender = normalize_sender(msg["sender"])

        if sender not in USER_SENDER_VALUES:
            i += 1
            continue

        episode = {
            "user_message_time": msg["timestamp"],
            "user_message_text": msg["text"],
            "assistant_response_time": None,
            "assistant_response_text": None,
            "consultation_end_time": msg["timestamp"],
        }

        j = i + 1
        while j < len(messages):
            next_msg = messages[j]
            next_sender = normalize_sender(next_msg["sender"])

            if next_sender in ASSISTANT_SENDER_VALUES:
                episode["assistant_response_time"] = next_msg["timestamp"]
                episode["assistant_response_text"] = next_msg["text"]
                episode["consultation_end_time"] = next_msg["timestamp"]
                break

            if next_sender in USER_SENDER_VALUES:
                break

            j += 1

        episodes.append(episode)
        i += 1

    return episodes


def get_latest_snapshot_at_or_before(
    target_ms: int, snapshots: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    latest = None
    for snap in snapshots:
        if snap["t_ms"] <= target_ms:
            latest = snap
        else:
            break
    return latest


def get_text_at_or_before(target_ms: int, snapshots: List[Dict[str, Any]]) -> str:
    snap = get_latest_snapshot_at_or_before(target_ms, snapshots)
    return snap["text"] if snap else ""


def get_session_end_ms(
    data: Dict[str, Any],
    snapshots: List[Dict[str, Any]],
    messages: List[Dict[str, Any]],
) -> Optional[int]:
    timestamps: List[int] = []
    timestamps.extend([s["t_ms"] for s in snapshots])
    timestamps.extend([m["timestamp"] for m in messages])

    submit_clicks = data.get("TimeStampOfSubmitClicks", [])
    if isinstance(submit_clicks, list):
        for ts in submit_clicks:
            if isinstance(ts, (int, float)):
                timestamps.append(int(ts))

    return max(timestamps) if timestamps else None


def compare_texts_word_level(text_before: str, text_after: str) -> Dict[str, Any]:
    before_words = text_before.split()
    after_words = text_after.split()
    matcher = SequenceMatcher(None, before_words, after_words)

    added_chunks: List[str] = []
    deleted_chunks: List[str] = []
    edited_from_chunks: List[str] = []
    edited_to_chunks: List[str] = []

    added_word_count = 0
    deleted_word_count = 0
    edited_from_word_count = 0
    edited_to_word_count = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            chunk = " ".join(after_words[j1:j2]).strip()
            if chunk:
                added_chunks.append(chunk)
                added_word_count += len(after_words[j1:j2])

        elif tag == "delete":
            chunk = " ".join(before_words[i1:i2]).strip()
            if chunk:
                deleted_chunks.append(chunk)
                deleted_word_count += len(before_words[i1:i2])

        elif tag == "replace":
            old_chunk = " ".join(before_words[i1:i2]).strip()
            new_chunk = " ".join(after_words[j1:j2]).strip()

            if old_chunk:
                edited_from_chunks.append(old_chunk)
                edited_from_word_count += len(before_words[i1:i2])

            if new_chunk:
                edited_to_chunks.append(new_chunk)
                edited_to_word_count += len(after_words[j1:j2])

    return {
        "added_text": " || ".join(added_chunks),
        "deleted_text": " || ".join(deleted_chunks),
        "edited_from_text": " || ".join(edited_from_chunks),
        "edited_to_text": " || ".join(edited_to_chunks),
        "added_word_count": added_word_count,
        "deleted_word_count": deleted_word_count,
        "edited_from_word_count": edited_from_word_count,
        "edited_to_word_count": edited_to_word_count,
        "net_word_change": count_words(text_after) - count_words(text_before),
    }


def compare_window_texts(
    snapshots: List[Dict[str, Any]],
    start_ms: int,
    end_ms: int,
) -> Optional[Dict[str, Any]]:
    start_text = get_text_at_or_before(start_ms, snapshots)
    end_text = get_text_at_or_before(end_ms, snapshots)

    if not start_text and not end_text:
        return None

    result = compare_texts_word_level(start_text, end_text)
    result["start_text"] = start_text
    result["end_text"] = end_text
    return result


def mean_or_none(values: List[int]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


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

    snapshots = get_editor_snapshots(data)
    messages = get_messages(data)
    participant_id = str(data.get("id", path.stem))
    session_end_ms = get_session_end_ms(data, snapshots, messages)
    episodes = get_consultation_episodes(messages)

    event_rows: List[Dict[str, Any]] = []

    pre_added_counts: List[int] = []
    post_added_counts: List[int] = []
    pre_deleted_counts: List[int] = []
    post_deleted_counts: List[int] = []
    pre_edited_counts: List[int] = []
    post_edited_counts: List[int] = []
    pre_net_changes: List[int] = []
    post_net_changes: List[int] = []

    for idx, ep in enumerate(episodes, start=1):
        user_time = ep["user_message_time"]
        consultation_end_time = ep["consultation_end_time"]

        pre_window = compare_window_texts(
            snapshots=snapshots,
            start_ms=max(0, user_time - PRE_WINDOW_MS),
            end_ms=user_time,
        )
        post_window = compare_window_texts(
            snapshots=snapshots,
            start_ms=consultation_end_time,
            end_ms=consultation_end_time + POST_WINDOW_MS,
        )

        pre_added = None if pre_window is None else pre_window["added_word_count"]
        post_added = None if post_window is None else post_window["added_word_count"]
        pre_deleted = None if pre_window is None else pre_window["deleted_word_count"]
        post_deleted = None if post_window is None else post_window["deleted_word_count"]
        pre_edited = None if pre_window is None else pre_window["edited_to_word_count"]
        post_edited = None if post_window is None else post_window["edited_to_word_count"]
        pre_net = None if pre_window is None else pre_window["net_word_change"]
        post_net = None if post_window is None else post_window["net_word_change"]

        if pre_added is not None:
            pre_added_counts.append(pre_added)
        if post_added is not None:
            post_added_counts.append(post_added)

        if pre_deleted is not None:
            pre_deleted_counts.append(pre_deleted)
        if post_deleted is not None:
            post_deleted_counts.append(post_deleted)

        if pre_edited is not None:
            pre_edited_counts.append(pre_edited)
        if post_edited is not None:
            post_edited_counts.append(post_edited)

        if pre_net is not None:
            pre_net_changes.append(pre_net)
        if post_net is not None:
            post_net_changes.append(post_net)

        relative_position = (
            round(user_time / session_end_ms, 4)
            if isinstance(session_end_ms, int) and session_end_ms > 0
            else None
        )

        event_rows.append({
            "participant_id": participant_id,
            "source_file": path.name,
            "consultation_number": idx,
            "user_message_time_ms": user_time,
            "assistant_response_time_ms": ep["assistant_response_time"],
            "consultation_end_time_ms": consultation_end_time,
            "relative_task_position": relative_position,
            "user_message_text": ep["user_message_text"],
            "assistant_response_text": ep["assistant_response_text"],

            "words_written_in_pre_window": pre_added,
            "words_written_in_post_window": post_added,
            "words_deleted_in_pre_window": pre_deleted,
            "words_deleted_in_post_window": post_deleted,
            "words_edited_in_pre_window": pre_edited,
            "words_edited_in_post_window": post_edited,
            "net_word_change_in_pre_window": pre_net,
            "net_word_change_in_post_window": post_net,

            "text_added_in_pre_window": None if pre_window is None else pre_window["added_text"],
            "text_deleted_in_pre_window": None if pre_window is None else pre_window["deleted_text"],
            "text_edited_from_in_pre_window": None if pre_window is None else pre_window["edited_from_text"],
            "text_edited_to_in_pre_window": None if pre_window is None else pre_window["edited_to_text"],

            "text_added_in_post_window": None if post_window is None else post_window["added_text"],
            "text_deleted_in_post_window": None if post_window is None else post_window["deleted_text"],
            "text_edited_from_in_post_window": None if post_window is None else post_window["edited_from_text"],
            "text_edited_to_in_post_window": None if post_window is None else post_window["edited_to_text"],

            "pre_window_ms": PRE_WINDOW_MS,
            "post_window_ms": POST_WINDOW_MS,
        })

    n_consultations = len(event_rows)

    summary_row = {
        "participant_id": participant_id,
        "source_file": path.name,
        "number_of_consultations": n_consultations,

        "mean_words_written_in_pre_window": mean_or_none(pre_added_counts),
        "mean_words_written_in_post_window": mean_or_none(post_added_counts),
        "mean_words_deleted_in_pre_window": mean_or_none(pre_deleted_counts),
        "mean_words_deleted_in_post_window": mean_or_none(post_deleted_counts),
        "mean_words_edited_in_pre_window": mean_or_none(pre_edited_counts),
        "mean_words_edited_in_post_window": mean_or_none(post_edited_counts),
        "mean_net_word_change_in_pre_window": mean_or_none(pre_net_changes),
        "mean_net_word_change_in_post_window": mean_or_none(post_net_changes),

        "has_messages_field": "messages" in data,
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

    print("Revision-after-consultation analysis completed.")
    print(f"Processed files: {len(files)}")
    print(f"Summary CSV saved to: {SUMMARY_OUTPUT_CSV}")
    print(f"Event-level CSV saved to: {EVENT_OUTPUT_CSV}")


if __name__ == "__main__":
    main()