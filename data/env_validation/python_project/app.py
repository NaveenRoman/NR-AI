import sqlite3, sys

def run_task():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE items (id INT, name TEXT)')
    conn.execute('INSERT INTO items VALUES (1, \'Validated Real Python\')')
    row = conn.execute('SELECT name FROM items WHERE id=1').fetchone()
    conn.close()
    return row[0]

if __name__ == '__main__':
    print(run_task())
