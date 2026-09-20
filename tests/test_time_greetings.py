"""
Tests for Time-Based Greeting and Welcome-Back logic.
"""

import unittest
from unittest.mock import patch
import datetime
from app.brain.companion import get_time_based_greeting


class TestTimeGreetings(unittest.TestCase):

    def test_morning_greeting(self):
        # 08:30 AM
        dt = datetime.datetime(2026, 9, 20, 8, 30, 0)
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            self.assertEqual(get_time_based_greeting(), "Good morning, Boss.")

    def test_afternoon_greeting(self):
        # 14:15 (2:15 PM)
        dt = datetime.datetime(2026, 9, 20, 14, 15, 0)
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            self.assertEqual(get_time_based_greeting(), "Good afternoon, Boss.")

    def test_evening_greeting(self):
        # 19:45 (7:45 PM)
        dt = datetime.datetime(2026, 9, 20, 19, 45, 0)
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            self.assertEqual(get_time_based_greeting(), "Good evening, Boss.")

    def test_late_night_greeting(self):
        # 23:30 (11:30 PM)
        dt = datetime.datetime(2026, 9, 20, 23, 30, 0)
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            self.assertEqual(get_time_based_greeting(), "Welcome back, Boss.")

    def test_session_resume_greeting(self):
        # Any hour with is_resume=True
        self.assertEqual(get_time_based_greeting(is_resume=True), "Welcome back, Boss.")


if __name__ == "__main__":
    unittest.main()
