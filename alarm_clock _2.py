"""
Simple CLI alarm clock.
Run with: python alarm_clock.py

The alarm checking runs in the background while the main thread handles
the menu. Saved alarms are stored in a small JSON file.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

# Use winsound on Windows if it is available
try:
    import winsound  
except ImportError:
    winsound = None

CONFIG_FILE = Path("alarms.json")
RING_SECONDS = 8

MENU = """
============== ALARM CLOCK ==============
 1. List alarms
 2. Add alarm
 3. Remove alarm
 4. Toggle alarm on/off
 5. Time until next alarm
 0. Quit
-----------------------------------------"""


# Helpers

def parse_time(raw_input: str) -> str:
    """Convert the entered time into HH:MM format."""
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", raw_input.strip().lower())
    if not match:
        raise ValueError("use a format like 7, 07:30, 7:30am or 19:45")

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)

    if meridiem:
        if not 1 <= hour <= 12:
            raise ValueError("hour must be 1-12 when using am/pm")
        if meridiem == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time is out of range")
    
    # Return the time in a consistent format
    return f"{hour:02d}:{minute:02d}"


def format_seconds(total_secs: float) -> str:
    total_secs = int(total_secs)
    hours, rest = divmod(total_secs, 3600)
    minutes, seconds = divmod(rest, 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if hours > 0 or minutes > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    
    return " ".join(parts)


# Alarm data and storage

@dataclass
class Alarm:
    id: int
    time: str  # Stored as HH:MM
    label: str = ""
    repeat_daily: bool = True
    enabled: bool = True

    def next_firing(self) -> datetime:
        now = datetime.now()
        hour, minute = map(int, self.time.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    def seconds_until(self) -> float:
        return (self.next_firing() - datetime.now()).total_seconds()

    def describe(self) -> str:
        state = "ON " if self.enabled else "OFF"
        repeat = "daily" if self.repeat_daily else "once"
        label_text = f" - {self.label}" if self.label else ""
        return f"#{self.id} [{state}] {self.time} ({repeat}){label_text}"


class AlarmStore:
    """Keeps track of alarms and saves them to a JSON file."""

    def __init__(self, file_path: Path) -> None:
        self.path = file_path
        self._lock = threading.Lock()
        self._alarms: list[Alarm] = []
        self._next_id = 1
        self._load()  # Load saved alarms when the app starts

    def snapshot(self) -> list[Alarm]:
        with self._lock:
            return list(self._alarms)

    def add(self, time_str: str, label: str, repeat_daily: bool) -> Alarm:
        with self._lock:
            alarm = Alarm(self._next_id, time_str, label, repeat_daily)
            self._next_id += 1
            self._alarms.append(alarm)
        self.save()
        return alarm

    def remove(self, alarm_id: int) -> bool:
        with self._lock:
            before_count = len(self._alarms)
            self._alarms = [a for a in self._alarms if a.id != alarm_id]
            removed = len(self._alarms) != before_count
            
        if removed:
            self.save()
        return removed

    def toggle(self, alarm_id: int) -> bool:
        found = False
        with self._lock:
            for alarm in self._alarms:
                if alarm.id == alarm_id:
                    alarm.enabled = not alarm.enabled
                    found = True
                    break
        if found:
            self.save()
            return True
        return False

    def disable(self, alarm_id: int) -> None:
        # Used when a one-time alarm has finished
        with self._lock:
            for alarm in self._alarms:
                if alarm.id == alarm_id:
                    alarm.enabled = False
        self.save()

    def save(self) -> None:
        with self._lock:
            data = [asdict(a) for a in self._alarms]
        try:
            self.path.write_text(json.dumps(data, indent=2))
        except OSError as e:
            print(f"ERROR: Could not save alarms to file: {e}")

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw_data = json.loads(self.path.read_text())
            # Turn the saved dictionaries back into Alarm objects
            self._alarms = []
            for item in raw_data:
                self._alarms.append(Alarm(**item))
        except Exception as e:
            print(f"Warning: Couldn't read saved alarms ({e}). Starting fresh.")
            self._alarms = []
            
        if self._alarms:
            # Continue from the highest existing ID
            self._next_id = max(a.id for a in self._alarms) + 1


# Alarm clock app

class AlarmClock:
    def __init__(self) -> None:
        self.store = AlarmStore(CONFIG_FILE)
        self._stop = threading.Event()
        # Keeps track of alarms that have already fired today
        self._monitor = threading.Thread(target=self._watch, daemon=True)

    def run(self) -> None:
        self._monitor.start()
        print("\nAlarm clock is armed. Press Ctrl+C to quit anytime.")
        
        # Connect menu choices to their functions
        actions = {
            "1": self._list,
            "2": self._add,
            "3": self._remove,
            "4": self._toggle,
            "5": self._next,
        }
        
        try:
            while True:
                print(MENU)
                choice = input("Choose an option: ").strip()
                if choice == "0":
                    break
                    
                action = actions.get(choice)
                if action:
                    action()
                else:
                    print("Invalid option, please choose between 0-5.")
                    
        except (KeyboardInterrupt, EOFError):
            print("\nCaught interrupt signal...")
        finally:
            self._stop.set()
            self.store.save()
            print("Alarms saved. Peace out!")

    # Menu actions
    
    def _list(self) -> None:
        alarms = self.store.snapshot()
        if not alarms:
            print("\n-> No alarms set yet. Try adding one!\n")
            return
        print()
        for alarm in alarms:
            print("  " + alarm.describe())
        print()

    def _add(self) -> None:
        raw_time = input("Enter time (e.g. 7, 07:30, 7:30am, 19:45): ")
        try:
            time_str = parse_time(raw_time)
        except ValueError as err:
            print(f"Invalid time format: {err}")
            return
            
        label = input("Optional label: ").strip()
        repeat_input = input("Repeat daily? [Y/n]: ").strip().lower()
        repeat = repeat_input != "n"
        
        alarm = self.store.add(time_str, label, repeat)
        print(f"Success! Alarm #{alarm.id} set for {alarm.time}.")

    def _ask_id(self, prompt_text: str) -> int | None:
        self._list()
        raw = input(prompt_text).strip()
        if not raw.isdigit():
            print("That's not a valid number ID.")
            return None
        return int(raw)

    def _remove(self) -> None:
        alarm_id = self._ask_id("Enter alarm id to remove: ")
        if alarm_id is None:
            return
            
        success = self.store.remove(alarm_id)
        if success:
            print("Alarm deleted.")
        else:
            print("Hmm, couldn't find an alarm with that ID.")

    def _toggle(self) -> None:
        alarm_id = self._ask_id("Enter alarm id to toggle: ")
        if alarm_id is None:
            return
            
        success = self.store.toggle(alarm_id)
        if success:
            print("Toggled alarm state.")
        else:
            print("No alarm found with that ID.")

    def _next(self) -> None:
        active_alarms = [a for a in self.store.snapshot() if a.enabled]
        if not active_alarms:
            print("\nNo enabled alarms found.\n")
            return
            
        soonest = min(active_alarms, key=lambda x: x.next_firing())
        time_left = format_seconds(soonest.seconds_until())
        print(f"\nNext up: #{soonest.id} at {soonest.time} (in {time_left})\n")

    # Background alarm checking
    
    def _watch(self) -> None:
        """Check the current time every second and fire matching alarms."""
        while not self._stop.is_set():
            now = datetime.now()
            today_date_str = now.strftime("%Y-%m-%d")
            current_time_str = now.strftime("%H:%M")

            for alarm in self.store.snapshot():
                if not alarm.enabled:
                    continue
                if alarm.time != current_time_str:
                    continue
                    
                # Prevent the same alarm from firing repeatedly during the minute
                if self._fired_on.get(alarm.id) == today_date_str:
                    continue
                    
                # Remember that this alarm has fired today
                self._fired_on[alarm.id] = today_date_str
                
                # Ring the alarm
                self._ring(alarm)
                
                # Turn off alarms that are not set to repeat
                if not alarm.repeat_daily:
                    self.store.disable(alarm.id)

            # Check again in one second
            self._stop.wait(1.0)

    def _ring(self, alarm: Alarm) -> None:
        # Show a clear message in the terminal
        banner_text = f"ALARM #{alarm.id} AT {alarm.time}"
        if alarm.label:
            banner_text += f" - {alarm.label}"
            
        print("\n" + "!" * 50)
        print(banner_text)
        print("!" * 50 + "\n")

        # Keep ringing for the configured amount of time
        end_time = datetime.now() + timedelta(seconds=RING_SECONDS)
        while datetime.now() < end_time and not self._stop.is_set():
            if winsound:
                # Short beep
                winsound.Beep(880, 300)
            else:
                # Terminal bell for systems without winsound
                print("\a", end="", flush=True)
            
            # Short pause between beeps
            self._stop.wait(0.7)


if __name__ == "__main__":
    clock = AlarmClock()
    clock.run()