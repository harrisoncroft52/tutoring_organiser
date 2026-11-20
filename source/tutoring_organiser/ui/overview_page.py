"""The Overview page: stats and earnings graph (mixed into MainWindow)."""
from datetime import date as Date, datetime, timedelta

from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
)

from ..config import DT_FMT
from ..helpers import effective_status, lesson_value, money, short_dt
from .charts import OverviewLineGraph
from .widgets import cell, ReflowContainer, ResizeWatcher, Stat


class OverviewPageMixin:
    """Methods for the Overview page. Mixed into MainWindow."""

    def build_overview_page(self):
        page = ResizeWatcher()
        page.setObjectName("root")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        welcome = QLabel("Welcome back")
        welcome.setObjectName("title")
        sub = QLabel("Here’s your tutoring at a glance.")
        sub.setObjectName("muted")
        titles.addWidget(welcome); titles.addWidget(sub)
        head.addLayout(titles, 1)
        for label, callback in (("Open calendar", self.show_calendar), ("New student", self.new_student)):
            button = QPushButton(label)
            if label == "New student": button.setObjectName("primary")
            button.clicked.connect(callback)
            head.addWidget(button)
        layout.addLayout(head)
        self.overview_stats = [Stat(x) for x in ("Students", "Lessons completed", "Earned this month", "Upcoming lessons")]
        layout.addWidget(ReflowContainer(self.overview_stats, min_item_width=150))
        lower = QBoxLayout(QBoxLayout.LeftToRight)
        activity_card = QFrame(); activity_card.setObjectName("card")
        al = QVBoxLayout(activity_card)
        self.overview_graph_title = QLabel("Lesson activity"); self.overview_graph_title.setObjectName("studentName")
        self.overview_graph = OverviewLineGraph()
        al.addWidget(self.overview_graph_title); al.addWidget(self.overview_graph, 1)
        graph_controls = QHBoxLayout()
        graph_controls.addStretch(1)
        self.graph_metric = QComboBox(); self.graph_metric.addItems(["Lesson activity", "Total revenue"])
        self.graph_period = QComboBox(); self.graph_period.addItems(["Last month", "All time"])
        self.graph_metric.setMaximumWidth(140); self.graph_period.setMaximumWidth(110)
        self.graph_period.setCurrentIndex(1)             # open on "All time"
        self.graph_metric.currentIndexChanged.connect(self.refresh_overview)
        self.graph_period.currentIndexChanged.connect(self.refresh_overview)
        graph_controls.addWidget(self.graph_metric); graph_controls.addWidget(self.graph_period)
        al.addLayout(graph_controls)
        upcoming_card = QFrame(); upcoming_card.setObjectName("card")
        ul = QVBoxLayout(upcoming_card)
        upcoming_title = QLabel("Coming up"); upcoming_title.setObjectName("studentName")
        self.upcoming_table = QTableWidget(0, 3)
        self.upcoming_table.setHorizontalHeaderLabels(["When", "Student", "Length"])
        self.upcoming_table.verticalHeader().setVisible(False)
        self.upcoming_table.setShowGrid(False)
        self.upcoming_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.upcoming_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.upcoming_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.upcoming_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.upcoming_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.upcoming_table.verticalHeader().setDefaultSectionSize(34)
        ul.addWidget(upcoming_title); ul.addWidget(self.upcoming_table, 1)
        activity_card.setMinimumHeight(250); upcoming_card.setMinimumHeight(220)
        lower.addWidget(activity_card, 3); lower.addWidget(upcoming_card, 2)
        layout.addLayout(lower, 1)
        # Narrow window: graph and "Coming up" stack vertically instead of squeezing side by side.
        page.resized.connect(lambda w: lower.setDirection(QBoxLayout.TopToBottom if w < 820 else QBoxLayout.LeftToRight))
        return page

    def refresh_overview(self):
        now = datetime.now()
        lessons = self.store.calendar_lessons()
        completed = [l for l in lessons if effective_status(l) == "completed"]
        month_earned = sum(lesson_value(l) for l in completed
                           if datetime.strptime(l["start"], DT_FMT).year == now.year
                           and datetime.strptime(l["start"], DT_FMT).month == now.month)
        upcoming = sorted((l for l in lessons if l["status"] != "cancelled"
                           and datetime.strptime(l["start"], DT_FMT) >= now), key=lambda l: l["start"])
        all_time = self.graph_period.currentIndex() == 1
        total_earned = sum(lesson_value(l) for l in completed)
        self.overview_stats[2].set_label("Earned all time" if all_time else "Earned this month")
        values = (str(len(self.store.students())), str(len(completed)),
                  money(total_earned if all_time else month_earned), str(len(upcoming)))
        for card, value in zip(self.overview_stats, values): card.set(value)
        revenue_mode = self.graph_metric.currentIndex() == 1
        self.overview_graph_title.setText(self.graph_metric.currentText())
        self.overview_graph.money_axis = revenue_mode
        lesson_points = []
        for lesson in completed:
            when = datetime.strptime(lesson["start"], DT_FMT)
            amount = lesson_value(lesson) if revenue_mode else 1
            lesson_points.append((when, amount))
        points = []
        if self.graph_period.currentIndex() == 0:
            first_day = now.date() - timedelta(days=29)
            buckets = [(first_day + timedelta(days=6 * i),
                        first_day + timedelta(days=6 * (i + 1)) if i < 4 else now.date() + timedelta(days=1))
                       for i in range(5)]
            for a, b in buckets:
                total = sum(v for d, v in lesson_points if a <= d.date() < b)
                last_day = b - timedelta(days=1)
                points.append((a.strftime("%d %b"), total, f"{a.day} {a:%b} \u2013 {last_day.day} {last_day:%b %Y}"))
        elif lesson_points:
            first = min(d for d, _ in lesson_points)
            month = Date(first.year, first.month, 1)
            last_month = Date(now.year, now.month, 1)
            while month <= last_month:
                next_month = Date(month.year + (month.month == 12), month.month % 12 + 1, 1)
                total = sum(v for d, v in lesson_points if month <= d.date() < next_month)
                points.append((month.strftime("%b %y"), total, month.strftime("%B %Y")))
                month = next_month
        self.overview_graph.set_values(points)
        self.upcoming_table.setRowCount(min(8, len(upcoming)))
        for i, lesson in enumerate(upcoming[:8]):
            self.upcoming_table.setItem(i, 0, cell(short_dt(lesson["start"])))
            self.upcoming_table.setItem(i, 1, cell(lesson["student_name"]))
            self.upcoming_table.setItem(i, 2, cell(f"{lesson['duration']} min"))
