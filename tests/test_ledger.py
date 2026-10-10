import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memorylab.cards import CanonicalCards,EvidenceStore
from memorylab.contracts import ContractError,read_kernel
from memorylab.ledger import Ledger
from memorylab.registry import Registry


class LedgerInitializationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'ledger.sqlite'
        with Ledger(self.path) as ledger:
            Registry(ledger,read_kernel())
            with ledger.mutation():
                ledger.commit_receipt('original',{'input':'unchanged'},{'status':'recorded'},'control')

    def tearDown(self):self.tmp.cleanup()

    def test_existing_sealed_store_and_contract_lookup_preserve_bytes_without_reinitializing(self):
        before=self.path.read_bytes()
        for readonly in (False,True,False):
            with Ledger(self.path,readonly=readonly) as ledger:
                original=ledger.audit();Registry(ledger,read_kernel());CanonicalCards(EvidenceStore(ledger))
                self.assertEqual(ledger.audit(),original)
            self.assertEqual(self.path.read_bytes(),before)
            self.assertFalse(Path(str(self.path)+'-wal').exists())

    def test_missing_table_or_renamed_column_is_rejected_without_repair_or_version_write(self):
        for sql in ('DROP TABLE events','ALTER TABLE receipts RENAME COLUMN payload TO invalid_payload'):
            with self.subTest(sql=sql):
                target=Path(self.tmp.name)/('broken-'+hashlib.sha256(sql.encode()).hexdigest()[:8]+'.sqlite')
                target.write_bytes(self.path.read_bytes())
                with sqlite3.connect(target) as connection:connection.execute(sql)
                connection.close();before=target.read_bytes()
                for readonly in (True,False):
                    with self.assertRaises(ContractError) as caught:Ledger(target,readonly=readonly)
                    self.assertEqual(caught.exception.code,'invalid_ledger_schema')
                    self.assertEqual(target.read_bytes(),before)
                    self.assertFalse(Path(str(target)+'-wal').exists())

    def test_unknown_version_is_refused_before_journal_conversion_or_schema_changes(self):
        with sqlite3.connect(self.path) as connection:connection.execute('PRAGMA user_version=2')
        connection.close();before=self.path.read_bytes()
        for readonly in (True,False):
            with self.assertRaises(ContractError) as caught:Ledger(self.path,readonly=readonly)
            self.assertEqual(caught.exception.code,'unsupported_ledger_schema')
            self.assertEqual(self.path.read_bytes(),before)

    def test_failed_initialization_rolls_back_partial_tables_and_can_restart_as_version_zero(self):
        target=Path(self.tmp.name)/'new.sqlite';original_connect=sqlite3.connect
        class FailingConnection:
            def __init__(self,*a,**kw):self.connection=original_connect(*a,**kw)
            def execute(self,*a,**kw):return self.connection.execute(*a,**kw)
            def close(self):return self.connection.close()
            def executescript(self,sql):
                self.connection.executescript(sql.split('CREATE TABLE IF NOT EXISTS receipts')[0])
                raise sqlite3.OperationalError('explicit initialization interruption')
        with patch('memorylab.ledger.sqlite3.connect',side_effect=FailingConnection):
            with self.assertRaises(sqlite3.OperationalError):Ledger(target)
        with original_connect(target) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],0)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[])
        connection.close()
        with Ledger(target) as ledger:self.assertEqual(ledger.db.execute('PRAGMA user_version').fetchone()[0],1)
        with Ledger(target,readonly=True) as ledger:self.assertEqual(ledger.head(),0)

    def test_invalid_partial_version_zero_schema_rolls_back_new_tables_and_version(self):
        target=Path(self.tmp.name)/'partial.sqlite'
        with sqlite3.connect(target) as connection:connection.execute('CREATE TABLE objects(wrong_column TEXT)')
        connection.close()
        with self.assertRaises(ContractError) as caught:Ledger(target)
        self.assertEqual(caught.exception.code,'invalid_ledger_schema')
        with sqlite3.connect(target) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],0)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[('objects',)])
        connection.close()
