# -*- coding: utf-8 -*-
import decimal
import glob
import os
import sqlite3
import tempfile
import unittest

from gpn.partition import _create_partition
from gpn.partition import _Connector
from gpn.partition import Partition
from gpn.partition import READ_ONLY
from gpn.partition import OUT_OF_MEMORY


class MkdtempTestCase(unittest.TestCase):
    # TestCase changes cwd to temporary location.  After testing,
    # removes files and restores original cwd.
    @classmethod
    def setUpClass(cls):
        cls._orig_dir = os.getcwd()
        cls._temp_dir = tempfile.mkdtemp()  # Requires mkdtemp--cannot
        os.chdir(cls._temp_dir)             # use TemporaryDirectory.

    @classmethod
    def tearDownClass(cls):
        cls._remove_tempfiles(cls)
        os.chdir(cls._orig_dir)
        os.rmdir(cls._temp_dir)

    def setUp(self):
        self._remove_tempfiles()

    def _remove_tempfiles(self):
        for path in glob.glob(os.path.join(self._temp_dir, '*')):
            os.remove(path)


class TestSQLiteSharedMemory(unittest.TestCase):
    """ """
    def setUp(self):
        self.memdb1_uri = 'file:memdb1?mode=memory&cache=shared'
        self.memdb1_conn = sqlite3.connect(self.memdb1_uri, uri=True)
        cursor = self.memdb1_conn.cursor()
        cursor.executescript("""
            CREATE TABLE testing (a, b);
            INSERT INTO testing VALUES ('foo', 'bar');
            INSERT INTO testing VALUES ('baz', 'qux');
        """)

        self.memdb2_uri = 'file:memdb2?mode=memory&cache=shared'
        self.memdb2_conn = sqlite3.connect(self.memdb2_uri, uri=True)
        cursor = self.memdb2_conn.cursor()
        cursor.executescript("""
            CREATE TABLE testing (a, b);
            INSERT INTO testing VALUES ('fee', 'fi');
            INSERT INTO testing VALUES ('fo', 'fum');
        """)

    def test_shared_memory(self):
        """Must provide connections to shared, in-memory databases."""
        def get_all_values(database):
            # Make new connection, query, close connection, return values.
            connection = sqlite3.connect(database, uri=True)
            cursor = connection.cursor()
            cursor.execute('SELECT * FROM testing')
            values = list(cursor.fetchall())
            connection.close()
            return values

        # Check memdb1.
        expected = [('foo', 'bar'), ('baz', 'qux')]
        self.assertEqual(expected, get_all_values(self.memdb1_uri))

        # Check memdb2.
        expected = [('fee', 'fi'), ('fo', 'fum')]
        self.assertEqual(expected, get_all_values(self.memdb2_uri))

        # Check memdb1, again.
        expected = [('foo', 'bar'), ('baz', 'qux')]
        self.assertEqual(expected, get_all_values(self.memdb1_uri))

    def test_original_closed(self):
        """Memory databases with zero open connections should be removed."""
        self.memdb1_conn.close()  # <- Closing only connection to memdb1.
        connection = sqlite3.connect(self.memdb1_uri, uri=True)
        cursor = connection.cursor()
        with self.assertRaises(sqlite3.OperationalError):
            cursor.execute('SELECT * FROM testing')


class TestConnector(MkdtempTestCase):
    def _get_tables(self, database):
        """Return tuple of expected tables and actual tables for given
        SQLite database."""
        if callable(database):
            connection = database()
        else:
            connection = sqlite3.connect(database)
        cursor = connection.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        actual_tables = {x[0] for x in cursor}
        connection.close()

        expected_tables = {
            'cell', 'hierarchy', 'label', 'cell_label', 'partition',
            'edge', 'edge_weight', 'relation', 'relation_weight', 'property',
            'sqlite_sequence'
        }

        return expected_tables, actual_tables

    def test_path_to_uri(self):
        # Basic path translation.
        uri = _Connector._path_to_uri('foo')
        self.assertEqual('file:foo', uri)

        uri = _Connector._path_to_uri('/foo')
        self.assertEqual('file:/foo', uri)

        uri = _Connector._path_to_uri('foo/../bar/')
        self.assertEqual('file:bar', uri)

        uri = _Connector._path_to_uri('/foo/../bar/')
        self.assertEqual('file:/bar', uri)

        # Query parameters.
        uri = _Connector._path_to_uri('foo', mode='ro')
        self.assertEqual('file:foo?mode=ro', uri)

        uri = _Connector._path_to_uri('foo', mode=None)
        self.assertEqual('file:foo', uri, 'None values must be removed.')

        uri = _Connector._path_to_uri('foo', mode='ro', cache='shared')
        self.assertEqual('file:foo?cache=shared&mode=ro', uri)

        # Special characters.
        uri = _Connector._path_to_uri('/foo?/bar#')
        self.assertEqual('file:/foo%3F/bar%23', uri)

        uri = _Connector._path_to_uri('foo', other='foo?bar#')
        self.assertEqual('file:foo?other=foo%3Fbar%23', uri)

    @unittest.skipUnless(os.name == 'nt', 'Windows-only path tests.')
    def test_win_path_to_uri(self):
        uri = _Connector._path_to_uri(r'foo\bar')
        self.assertEqual('file:foo/bar', uri)

        uri = _Connector._path_to_uri(r'C:\foo\bar')
        self.assertEqual('file:///C:/foo/bar', uri)

        uri = _Connector._path_to_uri(r'C:foo\bar')
        self.assertEqual('file:///C:/foo/bar', uri)

    def test_existing_database(self):
        """Existing database should load without errors."""
        global _create_partition

        database = 'partition_database'
        connection = sqlite3.connect(database)
        cursor = connection.cursor()
        cursor.executescript(_create_partition)  # Creating database.
        connection.close()

        connect = _Connector(database)  # Existing database.
        connection = connect()
        self.assertIsInstance(connection, sqlite3.Connection)

    def test_new_database(self):
        """If named database does not exist, it should be created."""
        database = 'partition_database'

        self.assertFalse(os.path.exists(database))  # File does not exist.

        connect = _Connector(database)
        self.assertTrue(os.path.exists(database))  # Now, file does exist.

        # Check that file contains expected tables.
        expected_tables, actual_tables = self._get_tables(database)
        self.assertSetEqual(expected_tables, actual_tables)

    def test_on_disk_temp_database(self):
        """Temporary databases can be created by omitting the path arg."""
        connect = _Connector()  # The `path` argument is omitted.
        filename = connect._temp_path

        # Check that database contains expected tables.
        expected_tables, actual_tables = self._get_tables(filename)
        self.assertSetEqual(expected_tables, actual_tables)

        # Make sure that temp file is removed up when object is deleted.
        self.assertTrue(os.path.exists(filename))  # Exists.
        del connect
        self.assertFalse(os.path.exists(filename))  # Does not exist.

    def test_in_memory_temp_database(self):
        """In-memory database."""
        connect = _Connector(mode='memory')
        self.assertIn('cache=shared&mode=memory', connect._uri)
        self.assertIsNone(connect._temp_path)
        self.assertIsInstance(connect._memory_conn, sqlite3.Connection)

        # Check that database contains expected tables.
        expected_tables, actual_tables = self._get_tables(connect)
        self.assertSetEqual(expected_tables, actual_tables)

        second_connect = _Connector(mode='memory')
        msg = 'A Second anonymous connection must not share the same name.'
        self.assertNotEqual(connect._uri, second_connect._uri, msg)

    def test_bad_sqlite_structure(self):
        """SQLite databases with unexpected table structure should fail."""
        filename = 'unknown_database.db'
        connection = sqlite3.connect(filename)
        cursor = connection.cursor()
        cursor.execute('CREATE TABLE foo (bar, baz)')
        connection.close()

        with self.assertRaises(Exception):
            connect = _Connector(filename)  # Attempt to load other SQLite file.

    def test_wrong_file_type(self):
        """Non-SQLite files should fail to load."""
        filename = 'test.txt'
        fh = open(filename, 'w')
        fh.write('This is a text file.')
        fh.close()

        with self.assertRaises(Exception):
            connect = _Connector(filename)  # Attempt to load non-SQLite file.


class TestSqlDataModel(MkdtempTestCase):
    def setUp(self):
        self.connection = Partition()._connect()

    def test_foreign_keys(self):
        cursor = self.connection.cursor()
        cursor.execute("INSERT INTO hierarchy VALUES (1, 'region', 0)")
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute("INSERT INTO label VALUES (1, 2, 'Midwest')")

    def test_cell_defaults(self):
        cursor = self.connection.cursor()
        cursor.execute('INSERT INTO cell DEFAULT VALUES')
        cursor.execute('SELECT * FROM cell')
        self.assertEqual([(1, '', 0)], cursor.fetchall())

    def test_label_autoincrement(self):
        cursor = self.connection.cursor()
        cursor.execute("INSERT INTO hierarchy VALUES (1, 'region', 0)")
        cursor.executescript("""
            INSERT INTO label VALUES (NULL, 1, 'Midwest');
            INSERT INTO label VALUES (NULL, 1, 'Northeast');
            INSERT INTO label VALUES (4,    1, 'South');  /* <- Explicit id. */
            INSERT INTO label VALUES (NULL, 1, 'West');
        """)
        cursor.execute('SELECT * FROM label')
        expected = [(1, 1, 'Midwest'),
                    (2, 1, 'Northeast'),
                    (4, 1, 'South'),
                    (5, 1, 'West')]
        self.assertEqual(expected, cursor.fetchall())

    def test_label_unique_constraint(self):
        cursor = self.connection.cursor()
        cursor.execute("INSERT INTO hierarchy VALUES (1, 'region', 0)")

        msg = 'Labels must be unique within their hierarchy level.'
        with self.assertRaises(sqlite3.IntegrityError, msg=msg):
            cursor.executescript("""
                INSERT INTO label VALUES (NULL, 1, 'Midwest');
                INSERT INTO label VALUES (NULL, 1, 'Midwest');
            """)

    def test_cell_label_foreign_key(self):
        cursor = self.connection.cursor()
        cursor.execute("INSERT INTO hierarchy VALUES (1, 'region', 0)")
        cursor.execute("INSERT INTO hierarchy VALUES (2, 'state',  1)")
        cursor.execute("INSERT INTO cell VALUES (1, '', 0)")
        cursor.execute("INSERT INTO label VALUES (1, 1, 'Midwest')")
        cursor.execute("INSERT INTO label VALUES (2, 2, 'Ohio')")

        msg = 'Mismatched hierarchy_id/label_id pairs must fail.'
        with self.assertRaises(sqlite3.IntegrityError, msg=msg):
            cursor.execute("INSERT INTO cell_label VALUES (1, 1, 1, 2)")

    def test_cell_label_unique_constraint(self):
        cursor = self.connection.cursor()
        cursor.execute("INSERT INTO hierarchy VALUES (1, 'region', 0)")
        cursor.execute("INSERT INTO hierarchy VALUES (2, 'state',  1)")
        cursor.execute("INSERT INTO cell VALUES (1, '', 0)")
        cursor.execute("INSERT INTO label VALUES (1, 1, 'Midwest')")
        cursor.execute("INSERT INTO cell_label VALUES (1, 1, 1, 1)")

        msg = 'Cells must never have two labels from the same hierarchy level.'
        with self.assertRaises(sqlite3.IntegrityError, msg=msg):
            cursor.execute("INSERT INTO cell_label VALUES (2, 1, 1, 1)")

    def test_denormalize_trigger(self):
        cursor = self.connection.cursor()
        cursor.execute("INSERT INTO hierarchy VALUES (1, 'region', 0)")
        cursor.execute("INSERT INTO hierarchy VALUES (2, 'state',  1)")
        cursor.execute("INSERT INTO cell VALUES (1, '', 0)")
        cursor.execute("INSERT INTO label VALUES (1, 1, 'Midwest')")
        cursor.execute("INSERT INTO label VALUES (2, 2, 'Ohio')")
        cursor.execute("INSERT INTO cell_label VALUES (1, 1, 1, 1)")
        cursor.execute("INSERT INTO cell_label VALUES (2, 1, 2, 2)")

        cursor.execute('SELECT * from cell')
        self.assertEqual([(1, '', 0)], cursor.fetchall())

        # Execute no-op UPDATE to activate trigger.
        cursor.execute("""UPDATE cell_label
                          SET cell_label_id=cell_label_id
                          WHERE cell_label_id = last_insert_rowid()""")

        cursor.execute('SELECT * from cell')
        self.assertEqual([(1, '1,2', 0)], cursor.fetchall())

    def test_textnum_decimal_type(self):
        """Decimal type values should be adapted as strings for TEXTNUM
        columns.  Fetched TEXTNUM values should be converted to Decimal
        types.

        """
        cursor = self.connection.cursor()
        cursor.execute('CREATE TEMPORARY TABLE test (weight TEXTNUM)')
        cursor.execute('INSERT INTO test VALUES (?)', (decimal.Decimal('1.1'),))
        cursor.execute('INSERT INTO test VALUES (?)', (decimal.Decimal('2.2'),))

        cursor.execute('SELECT * FROM test')
        expected = [(decimal.Decimal('1.1'),), (decimal.Decimal('2.2'),)]
        msg = 'TEXTNUM values must be converted to Decimal type.'
        self.assertEqual(expected, cursor.fetchall(), msg)


class TestPartition(MkdtempTestCase):
    def test_existing_partition(self):
        """Existing partition should load without errors."""
        global _create_partition

        filename = 'existing_partition'
        connection = sqlite3.connect(filename)
        cursor = connection.cursor()
        cursor.executescript(_create_partition)  # Creating existing partition.
        connection.close()

        ptn = Partition(filename)  # Use existing file.

    def test_read_only_partition(self):
        """Existing partition should load without errors."""
        global _create_partition

        filename = 'existing_partition'
        connection = sqlite3.connect(filename)
        cursor = connection.cursor()
        cursor.executescript(_create_partition)  # Creating existing partition.
        connection.close()

        with self.assertRaises(sqlite3.OperationalError):
            ptn = Partition(filename, flags=READ_ONLY)
            connection = ptn._connect()
            cursor = connection.cursor()
            cursor.execute('INSERT INTO cell DEFAULT VALUES')

    def test_new_partition(self):
        filename = 'new_partition'

        self.assertFalse(os.path.exists(filename))
        ptn = Partition(filename)  # Create new file.
        del ptn
        self.assertTrue(os.path.exists(filename))

    def test_temporary_partition(self):
        # In memory.
        ptn = Partition()
        self.assertIsNone(ptn._connect._temp_path)
        self.assertIsNotNone(ptn._connect._memory_conn)

        # On disk.
        ptn = Partition(flags=OUT_OF_MEMORY)
        self.assertIsNotNone(ptn._connect._temp_path)
        self.assertIsNone(ptn._connect._memory_conn)


if __name__ == '__main__':
    unittest.main()
