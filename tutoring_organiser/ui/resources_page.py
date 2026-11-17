"""Online resources browser and the My resources PDF library (mixed into MainWindow)."""
import json
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..helpers import norm
from ..parsing import pdfs_in
from .dialogs import ManageSitesDialog
from .widgets import cell, make_table


try:  # Included with PySide6-Addons; the resource page still offers direct links without it.
    from PySide6.QtWebEngineCore import (QWebEngineDownloadRequest, QWebEngineProfile,
                                         QWebEngineUrlRequestInterceptor)
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAVE_WEBENGINE = True
except ImportError:
    HAVE_WEBENGINE = False


if HAVE_WEBENGINE:
    class StatCounterRequestFilter(QWebEngineUrlRequestInterceptor):
        """Block the specific third-party script responsible for the console warning."""
        def interceptRequest(self, info):
            url = info.requestUrl()
            if (url.host().lower() == "secure.statcounter.com" and
                    url.path() == "/counter/counter.js"):
                info.block(True)


DEFAULT_RESOURCE_SITES = [
    ("Man vs Maths", "https://manvsmaths.com/"),
    ("No Brain Too Small", "https://www.nobraintoosmall.co.nz/"),
    ("NZ Scholarship Network", "https://nsn.nz/"),
]


def load_resource_sites(store):
    """The user's website list from app_settings, or the built-in three the first time."""
    raw = store.setting("online_resource_sites", "")
    if not raw:
        return list(DEFAULT_RESOURCE_SITES)
    try:
        sites = [(str(name), str(url)) for name, url in json.loads(raw) if name and url]
    except (ValueError, TypeError):
        return list(DEFAULT_RESOURCE_SITES)
    return sites


def save_resource_sites(store, sites):
    store.set_setting("online_resource_sites", json.dumps([[name, url] for name, url in sites]))


class ResourcesPageMixin:
    """Methods for the Online resources and My resources pages. Mixed into MainWindow."""

    def build_online_page(self):
        page = QWidget(); page.setObjectName("root")
        layout = QVBoxLayout(page); layout.setContentsMargins(28, 22, 28, 20); layout.setSpacing(10)
        heading = QLabel("Online resources"); heading.setObjectName("title")
        layout.addWidget(heading)
        self.resource_sites = load_resource_sites(self.store)
        toolbar = QHBoxLayout()
        self.site_choice = QComboBox(); self.site_choice.setMinimumWidth(170)
        self.site_choice.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.site_choice.currentTextChanged.connect(self.on_site_chosen)
        self.resource_address = QLineEdit(); self.resource_address.setPlaceholderText("Search this site or enter a web address")
        self.resource_address.returnPressed.connect(self.resource_search)
        go = QPushButton("Search / go"); go.setObjectName("primary"); go.clicked.connect(self.resource_search)
        home = QPushButton("Site home"); home.clicked.connect(self.resource_home)
        manage = QPushButton("Manage websites\u2026"); manage.clicked.connect(self.manage_resource_sites)
        manage.setToolTip("Add, remove, rename or reorder the websites in this list")
        toolbar.addWidget(self.site_choice); toolbar.addWidget(self.resource_address, 1)
        toolbar.addWidget(go); toolbar.addWidget(home); toolbar.addWidget(manage)
        layout.addLayout(toolbar)
        self.fill_site_choice(self.store.setting("online_resource_last", ""))
        if HAVE_WEBENGINE:
            self.web_view = QWebEngineView()
            start = self.current_site_url()
            if start:
                self.web_view.setUrl(QUrl(start))
            back = QPushButton("Back"); back.clicked.connect(self.web_view.back)
            forward = QPushButton("Forward"); forward.clicked.connect(self.web_view.forward)
            toolbar.insertWidget(1, back); toolbar.insertWidget(2, forward)
            layout.addWidget(self.web_view, 1)
            self.downloads_label = QLabel("Downloaded PDFs appear here for dragging onto a lesson row.")
            self.downloads_label.setObjectName("muted")
            self.download_list = make_table(["Downloaded PDF — drag onto a lesson row"], stretch_col=0,
                                            pdf_drag_column=0, whole_row_drag=True)
            self.download_list.setMaximumHeight(140)
            self.download_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.download_list.doubleClicked.connect(self.open_downloaded_resource)
            layout.addWidget(self.downloads_label); layout.addWidget(self.download_list)
            profile = QWebEngineProfile.defaultProfile()
            # The embedded reference sites can include StatCounter's legacy
            # document.write counter script. Block only that known analytics
            # resource; other page and Chromium diagnostics remain visible.
            self._statcounter_filter = StatCounterRequestFilter()
            profile.setUrlRequestInterceptor(self._statcounter_filter)
            profile.downloadRequested.connect(self.handle_resource_download)
            self.online_note = QLabel("Downloads are temporary until attached to a lesson. Drag a downloaded PDF onto the lesson row.")
            self.online_links = None
        else:
            self.web_view = None
            self.online_note = QLabel("The embedded browser needs PySide6-Addons (Qt WebEngine). These site links still open in your default browser.")
            self.online_links = QVBoxLayout()
            layout.addWidget(self.online_note)
            layout.addLayout(self.online_links)
            layout.addStretch(1)
            self.fill_online_links()
            self.download_list = None
        self.online_note.setObjectName("muted")
        self.online_note.setWordWrap(True)
        if HAVE_WEBENGINE:
            layout.addWidget(self.online_note)
        return page

    # ---- the website list
    def fill_site_choice(self, keep=""):
        self.site_choice.blockSignals(True)
        self.site_choice.clear()
        for name, url in self.resource_sites:
            self.site_choice.addItem(name, url)
            self.site_choice.setItemData(self.site_choice.count() - 1, url, Qt.ToolTipRole)
        index = self.site_choice.findText(keep)
        self.site_choice.setCurrentIndex(index if index >= 0 else 0)
        self.site_choice.blockSignals(False)
        if not self.resource_sites:
            self.site_choice.setPlaceholderText("No websites yet")

    def fill_online_links(self):
        """Fallback without the embedded browser: one 'Open' button per website."""
        if self.online_links is None:
            return
        while self.online_links.count():
            item = self.online_links.takeAt(0)
            if item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
                item.layout().deleteLater()
        for name, url in self.resource_sites:
            row = QHBoxLayout()
            link = QPushButton(f"Open {name}"); link.clicked.connect(lambda _, u=url: QDesktopServices.openUrl(QUrl(u)))
            row.addWidget(link); row.addWidget(QLabel(url), 1); self.online_links.addLayout(row)

    def current_site_url(self):
        return self.site_choice.currentData() or ""

    def on_site_chosen(self, name):
        if name:
            self.store.set_setting("online_resource_last", name)
        self.resource_home()

    def manage_resource_sites(self):
        current = None
        if self.web_view is not None and self.web_view.url().isValid() and self.web_view.url().scheme().startswith("http"):
            current = (self.web_view.title().strip(), self.web_view.url().toString())
        dlg = ManageSitesDialog(self, self.resource_sites, DEFAULT_RESOURCE_SITES, current)
        if not dlg.exec() or dlg.result_sites is None:
            return
        before = self.site_choice.currentText()
        self.resource_sites = dlg.result_sites
        save_resource_sites(self.store, self.resource_sites)
        self.fill_site_choice(before)
        self.fill_online_links()
        if self.site_choice.currentText() != before:      # the open site was removed or renamed
            self.resource_home()
        self.toast.show_message(f"Saved {len(self.resource_sites)} website(s).", "ok")

    def resource_search(self):
        query = self.resource_address.text().strip()
        if not query: return self.resource_home()
        if query.startswith(("https://", "http://")):
            url = query
        else:
            from urllib.parse import quote_plus
            from urllib.parse import urlparse
            domain = urlparse(self.current_site_url()).netloc
            url = "https://www.google.com/search?q=" + quote_plus(f"site:{domain} {query}" if domain else query)
        if self.web_view:
            self.web_view.setUrl(QUrl(url))
        else:
            QDesktopServices.openUrl(QUrl(url))

    def resource_home(self):
        url = self.current_site_url()
        if not url:
            return
        if self.web_view: self.web_view.setUrl(QUrl(url))
        else: QDesktopServices.openUrl(QUrl(url))

    def handle_resource_download(self, download):
        name = Path(download.downloadFileName()).name or f"resource_{uuid.uuid4().hex[:8]}.pdf"
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        download.setDownloadDirectory(self.download_temp.name)
        download.setDownloadFileName(name)
        download.stateChanged.connect(lambda state, d=download: self.resource_download_state(d, state))
        download.accept()

    def resource_download_state(self, download, state):
        if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            path = Path(download.downloadDirectory()) / download.downloadFileName()
            if path.is_file() and path.suffix.lower() == ".pdf" and self.download_list:
                row = self.download_list.rowCount(); self.download_list.insertRow(row)
                item = cell(path.name, data=str(path)); item.setToolTip(str(path)); item.setData(Qt.UserRole + 1, str(path))
                self.download_list.setItem(row, 0, item)
                self.online_note.setText(f"Ready to drag: {path.name}. Attach it to a lesson row to keep a copy with your lesson.")

    def open_downloaded_resource(self, index):
        path = self.download_list.item(index.row(), 0).data(Qt.UserRole)
        if path and Path(path).is_file(): QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def build_resources_page(self):
        page = QWidget()
        page.setObjectName("root")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        heading = QLabel("My resources")
        heading.setObjectName("title")
        row = QHBoxLayout()
        self.resource_path = QLineEdit()
        self.resource_path.setReadOnly(True)
        self.resource_path.setPlaceholderText("Choose a folder to search for PDFs")
        choose = QPushButton("Link folder")
        choose.clicked.connect(self.choose_resource_folder)
        scan = QPushButton("Search for PDFs")
        scan.clicked.connect(self.refresh_resources)
        row.addWidget(self.resource_path, 1); row.addWidget(choose); row.addWidget(scan)
        self.resource_list = make_table(["PDF file", "Folder"], stretch_col=0, pdf_drag_column=0, whole_row_drag=True)
        self.resource_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.resource_list.doubleClicked.connect(self.open_resource_pdf)
        self.resource_note = QLabel("")
        self.resource_note.setObjectName("muted")
        drag_hint = QLabel("Drag one or more PDFs from this list into an email, a browser upload, a folder or "
                           "another app. Double-click a PDF to open it.")
        drag_hint.setObjectName("muted")
        layout.addWidget(heading); layout.addLayout(row); layout.addWidget(drag_hint); layout.addWidget(self.resource_note)
        layout.addWidget(self.resource_list, 1)
        saved = self.store.con.execute("SELECT value FROM app_settings WHERE key='resources_folder'").fetchone()
        if saved:
            self.resource_path.setText(saved["value"])
            self.refresh_resources()
        return page

    def choose_resource_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose your resources folder", self.resource_path.text())
        if folder:
            self.store.con.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('resources_folder',?)", (folder,))
            self.store.con.commit()
            self.resource_path.setText(folder)
            self.refresh_resources()

    def refresh_resources(self):
        folder = self.resource_path.text().strip()
        every = pdfs_in(folder) if folder and Path(folder).is_dir() else []
        linked = self.store.linked_pdf_paths()
        files = [f for f in every if norm(str(f)) not in linked and norm(str(f.resolve())) not in linked]
        hidden = len(every) - len(files)
        self.resource_note.setText(f"{hidden} PDF(s) hidden because they're already linked to a student." if hidden else "")
        self.resource_list.setRowCount(len(files))
        for i, path in enumerate(files):
            item = cell(path.name, data=str(path))
            item.setToolTip(str(path))
            item.setData(Qt.UserRole + 1, str(path))          # what gets dragged out
            self.resource_list.setItem(i, 0, item)
            self.resource_list.setItem(i, 1, cell(str(path.parent)))

    def show_resources(self):
        self.refresh_resources()
        self.show_page(self.pages.indexOf(self.resources_page))

    def open_resource_pdf(self, index):
        path = self.resource_list.item(index.row(), 0).data(Qt.UserRole)
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
