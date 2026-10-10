"""Durable optimistic transactions. Every reservation and lock shares one commit."""
from copy import deepcopy
from threading import RLock
from .errors import Conflict

class MemoryStore:
    """Only local unit tests; never selected by a deployed Lambda."""
    def __init__(self):
        self.rows = {}
        self.lock = RLock()

    def get(self, key):
        with self.lock:
            return deepcopy(self.rows.get(key))

    def commit(self, rows, expected):
        with self.lock:
            if any((self.rows.get(k) or {}).get("version") != v for k, v in expected.items()):
                raise Conflict()
            for row in rows:
                row = deepcopy(row)
                row["version"] = (expected[row["pk"]] or 0) + 1
                self.rows[row["pk"]] = row

    def list_owner(self, owner, kind):
        with self.lock:
            return deepcopy([v for v in self.rows.values() if v.get("owner_id") == owner and v.get("kind") == kind])

    def scan_kind(self, kind):
        with self.lock:
            return deepcopy([v for v in self.rows.values() if v.get("kind") == kind])

class DynamoStore:
    def __init__(self, table, client=None):
        import boto3
        from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
        self.client = client or boto3.client("dynamodb")
        self.table = table
        self.ser, self.des = TypeSerializer(), TypeDeserializer()

    def encode(self, value):
        return {k: self.ser.serialize(v) for k, v in value.items()}

    def decode(self, value):
        return {k: self.des.deserialize(v) for k, v in value.items()}

    def get(self, key):
        r = self.client.get_item(TableName=self.table, Key={"pk": {"S": key}}, ConsistentRead=True)
        return self.decode(r["Item"]) if "Item" in r else None

    def commit(self, rows, expected):
        from botocore.exceptions import ClientError
        actions = []
        for row in rows:
            row = deepcopy(row)
            old = expected[row["pk"]]
            row["version"] = (old or 0) + 1
            put = {"TableName": self.table, "Item": self.encode(row),
                   "ConditionExpression": "attribute_not_exists(pk)" if old is None else "#v = :v"}
            if old is not None:
                put.update(ExpressionAttributeNames={"#v": "version"}, ExpressionAttributeValues={":v": {"N": str(old)}})
            actions.append({"Put": put})
        try:
            self.client.transact_write_items(TransactItems=actions)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"TransactionCanceledException", "TransactionConflictException"}:
                raise Conflict() from exc
            raise

    def list_owner(self, owner, kind):
        rows, start = [], {}
        while True:
            r = self.client.query(TableName=self.table, IndexName="by_owner",
                KeyConditionExpression="owner_id = :o AND begins_with(owner_sort, :k)",
                ExpressionAttributeValues={":o": {"S": owner}, ":k": {"S": kind + "#"}}, **start)
            rows.extend(self.decode(i) for i in r["Items"])
            if "LastEvaluatedKey" not in r:
                return rows
            start = {"ExclusiveStartKey": r["LastEvaluatedKey"]}

    def scan_kind(self, kind):
        rows, start = [], {}
        while True:
            r = self.client.scan(TableName=self.table, FilterExpression="#k = :k",
                ExpressionAttributeNames={"#k": "kind"}, ExpressionAttributeValues={":k": {"S": kind}}, **start)
            rows.extend(self.decode(i) for i in r["Items"])
            if "LastEvaluatedKey" not in r:
                return rows
            start = {"ExclusiveStartKey": r["LastEvaluatedKey"]}
