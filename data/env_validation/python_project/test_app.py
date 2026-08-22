import unittest
from app import run_task

class TestApp(unittest.TestCase):
    def test_run(self):
        self.assertEqual(run_task(), 'Validated Real Python')

if __name__ == '__main__':
    unittest.main()
