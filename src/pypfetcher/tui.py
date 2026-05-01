import subprocess
import shutil
import threading
from importlib.resources import as_file, files
from pathlib import Path
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, DataTable, Static, ListItem, ListView, Label
from textual.containers import Horizontal, Vertical, Container
from textual.binding import Binding
from .data import PaperIndex, normalize_filter_value, semester_label


def resolve_index_path():
    local_index = Path("index.json")
    if local_index.exists():
        return local_index, None

    resource = files("pypfetcher") / "index.json"
    context = as_file(resource)
    return Path(context.__enter__()), context


class PaperTUI(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    #search-container {
        height: 3;
        dock: top;
        padding: 0 1;
    }

    #main-container {
        layout: horizontal;
    }

    #sidebar {
        width: 34;
        min-width: 28;
        padding: 0 1;
        height: 1fr;
        overflow-y: auto;
    }

    #program-list, #semester-list {
        height: auto;
        max-height: 12;
        margin-bottom: 1;
    }

    #year-range-row {
        height: auto;
        width: 100%;
        layout: horizontal;
        align: center middle;
        margin-bottom: 1;
    }

    .year-stepper {
        width: 1fr;
        min-width: 8;
    }

    #year-range-separator {
        width: 3;
        content-align: center middle;
    }

    #results-table {
        height: 1fr;
    }

    #status-bar {
        height: 1;
        padding: 0 1;
    }

    .filter-label {
        padding: 1 1 0 1;
        text-style: bold;
    }

    .active-filter {
        background: $accent;
        color: $text;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("enter", "download", "Download"),
        Binding("f", "focus_search", "Search"),
        Binding("r", "refresh", "Reload"),
        Binding("escape", "blur_search", "Blur Search"),
    ]

    def __init__(self, index_path="index.json"):
        super().__init__()
        if index_path == "index.json":
            self.index_path, self._index_context = resolve_index_path()
        else:
            self.index_path = Path(index_path)
            self._index_context = None
        self.index = PaperIndex(self.index_path)
        year_min, year_max = self.index.get_year_bounds()
        self.year_min = year_min if year_min is not None else 2022
        self.year_max = year_max if year_max is not None else 2025
        # Defaults: 2022-2025, B.Tech
        self.active_filters = {
            "year_start": max(2022, self.year_min),
            "year_end": min(2025, self.year_max),
            "program": "B.Tech",
            "semester": None
        }

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="search-container"):
            yield Input(placeholder="Search papers (e.g. 'Data Structures', '2155')...", id="search-input")
        
        with Horizontal(id="main-container"):
            with Vertical(id="sidebar"):
                yield Label("DEGREE", classes="filter-label")
                yield ListView(id="program-list")
                yield Label("SEMESTER", classes="filter-label")
                yield ListView(id="semester-list")
                yield Label("YEAR RANGE", classes="filter-label")
                with Horizontal(id="year-range-row"):
                    yield Input(value=str(self.active_filters["year_start"]), id="year-start", classes="year-stepper")
                    yield Label("-", id="year-range-separator")
                    yield Input(value=str(self.active_filters["year_end"]), id="year-end", classes="year-stepper")
            
            yield DataTable(id="results-table")
        
        yield Static("Ready", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Year", "Program", "Sem", "Name")
        table.cursor_type = "row"
        self.populate_filters()
        self.sync_year_inputs()
        self.update_results()

        # Check wget availability and warn
        if shutil.which("wget") is None:
            self.notify("wget not found — downloads will fail", severity="warning")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self.update_results(event.value)
        elif event.input.id in {"year-start", "year-end"}:
            self.handle_year_input(event.input.id, event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not event.item or not event.item.name:
            return
        
        kind, value = event.item.name.split(":", 1)
        if kind == "program":
            self.active_filters["program"] = value
        elif kind == "semester":
            # Toggle semester
            if self.active_filters.get("semester") == value:
                self.active_filters["semester"] = None
            else:
                self.active_filters["semester"] = value
        
        self.refresh_sidebar_visuals()
        self.update_results()

    def refresh_sidebar_visuals(self) -> None:
        for item in self.query("ListItem"):
            if not item.name: continue
            kind, value = item.name.split(":", 1)
            active = normalize_filter_value(kind, self.active_filters.get(kind))
            candidate = normalize_filter_value(kind, value)
            if active and active == candidate:
                item.add_class("active-filter")
            else:
                item.remove_class("active-filter")

    def sync_year_inputs(self) -> None:
        self.query_one("#year-start", Input).value = str(self.active_filters["year_start"])
        self.query_one("#year-end", Input).value = str(self.active_filters["year_end"])

    def handle_year_input(self, input_id: str, value: str) -> None:
        value = value.strip()
        if not value:
            return

        try:
            year = int(value)
        except ValueError:
            return

        year = max(self.year_min, min(self.year_max, year))
        changed = False

        if input_id == "year-start":
            year = min(year, self.active_filters["year_end"])
            if year != self.active_filters["year_start"]:
                self.active_filters["year_start"] = year
                changed = True
        else:
            year = max(year, self.active_filters["year_start"])
            if year != self.active_filters["year_end"]:
                self.active_filters["year_end"] = year
                changed = True

        if changed:
            self.update_results()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"year-start", "year-end"}:
            self.handle_year_input(event.input.id, event.value)
            self.sync_year_inputs()

    def update_results(self, query: str = "") -> None:
        table = self.query_one(DataTable)
        # Store current row index if possible to restore? No, simplicity first.
        table.clear()

        if not self.index.index_exists:
            table.add_row("-", "-", "-", f"index.json missing at {self.index.path}. Run: pyp-crawl --start-year 2022")
            self.query_one("#status-bar").update("Index missing")
            return

        if self.index.load_error:
            table.add_row("-", "-", "-", f"Could not load index.json: {self.index.load_error}")
            self.query_one("#status-bar").update("Index load failed")
            return

        query = query.lower() if query else self.query_one("#search-input").value.lower()
        papers = self.index.search(query, self.active_filters)

        if not papers:
            table.add_row("-", "-", "-", "No matching papers")
            self.query_one("#status-bar").update("Found 0 papers")
            return

        for p in papers[:500]:
            attrs = p.get("attributes", {})
            table.add_row(
                str(attrs.get("year", "-")),
                str(attrs.get("program", "-")),
                semester_label(attrs.get("semester", "-")),
                str(p.get("filename", "Unknown")),
                key=p.get("url")
            )

        self.query_one("#status-bar").update(f"Found {len(papers)} papers")

    def populate_filters(self) -> None:
        options = self.index.get_filter_options()
        
        p_list = self.query_one("#program-list")
        p_list.clear()
        for p in options.get("program", []):
            p_list.append(ListItem(Label(p), name=f"program:{p}"))
            
        s_list = self.query_one("#semester-list")
        s_list.clear()
        for s in options.get("semester", []):
            s_list.append(ListItem(Label(s), name=f"semester:{s}"))
            
        self.refresh_sidebar_visuals()

    def action_focus_search(self) -> None:
        self.query_one("#search-input").focus()

    def action_blur_search(self) -> None:
        try:
            self.query_one(DataTable).focus()
        except Exception:
            pass

    def _watch_download(self, process: subprocess.Popen, filename: str) -> None:
        return_code = process.wait()

        def finish_notice() -> None:
            if return_code == 0:
                self.query_one("#status-bar").update(f"Done: {filename}")
                self.notify(f"Done: {filename}")
            else:
                self.query_one("#status-bar").update(f"Download failed: {filename}")
                self.notify(f"Download failed: {filename}", severity="error")

        self.call_from_thread(finish_notice)

    def _download_selected_paper(self) -> None:
        table = self.query_one(DataTable)
        try:
            coordinate = table.cursor_coordinate
            row_key = table.coordinate_to_cell_key(coordinate).row_key
            if not row_key:
                self.notify("Select a paper first", severity="warning")
                return
            url = row_key.value
        except Exception:
            self.notify("Select a paper first", severity="warning")
            return
        
        paper = next((p for p in self.index.papers if p["url"] == url), None)
        if not paper: return

        wget_path = shutil.which("wget")
        if wget_path is None:
            self.notify("wget not found — install wget to download files", severity="error")
            return

        filename = paper["filename"]
        self.query_one("#status-bar").update(f"Downloading {filename}...")

        try:
            # Ensure we are in current directory
            process = subprocess.Popen(
                [wget_path, "-c", url, "-O", filename],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.notify(f"Downloading: {filename}")
            threading.Thread(
                target=self._watch_download,
                args=(process, filename),
                daemon=True,
            ).start()
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "results-table":
            self._download_selected_paper()

    def action_download(self) -> None:
        self._download_selected_paper()

    def action_refresh(self) -> None:
        self.index.load()
        year_min, year_max = self.index.get_year_bounds()
        if year_min is not None and year_max is not None:
            self.year_min = year_min
            self.year_max = year_max
            self.active_filters["year_start"] = max(self.year_min, min(self.active_filters["year_start"], self.year_max))
            self.active_filters["year_end"] = max(self.active_filters["year_start"], min(self.active_filters["year_end"], self.year_max))
        self.populate_filters()
        self.sync_year_inputs()
        self.update_results()
        self.notify("Index reloaded")

    def on_unmount(self) -> None:
        if self._index_context is not None:
            self._index_context.__exit__(None, None, None)

def main():
    app = PaperTUI()
    app.run()

if __name__ == "__main__":
    main()
