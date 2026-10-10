import json
import os
from functools import lru_cache
from .cost import Estimator
from .service import Service
from .store import DynamoStore
from .driver_router import DriverRouter

def build_service():
    return Service(DynamoStore(os.environ["TABLE_NAME"]), estimator=Estimator(load_prices(os.getenv('PRICES_PARAMETER'))),
        verified=filter(None, os.getenv("VERIFIED_TEMPLATES", "").split(",")),
        validation=filter(None, os.getenv("VALIDATION_TEMPLATES", "").split(",")),
        user_limit=int(os.getenv("USER_LIMIT", "2")), total_limit=int(os.getenv("TOTAL_LIMIT", "4")),
        budget_milliusd=int(os.environ["BUDGET_MILLIUSD"]),workflow_terminal=workflow_terminal)

@lru_cache(maxsize=1)
def load_prices(parameter_name):
    if not parameter_name:return json.loads(os.environ['PRICES_JSON'])
    import boto3
    from botocore.config import Config
    client=boto3.client('ssm',config=Config(connect_timeout=.4,read_timeout=.7,retries={'total_max_attempts':1}))
    return json.loads(client.get_parameter(Name=parameter_name,WithDecryption=False)['Parameter']['Value'])

def build_driver():
    return DriverRouter(json.loads(os.environ["DRIVER_SETTINGS"]))

def dispatch(oid):
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    client = boto3.client("stepfunctions", config=Config(connect_timeout=0.4, read_timeout=0.7, retries={"total_max_attempts": 1}))
    try:
        op=build_service().store.get('O#'+oid)
        return client.start_execution(stateMachineArn=os.environ["STATE_MACHINE_ARN"], name=execution_name(op),
            input=json.dumps({"operation_id": oid,'generation':int(op.get('generation',0))}, separators=(",", ":")))
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ExecutionAlreadyExists":
            raise

def execution_name(op):
    return op['operation_id']+('-r'+str(op['generation']) if op.get('generation') else '')

def workflow_terminal(op):
    import boto3
    from botocore.exceptions import ClientError
    arn=os.environ['STATE_MACHINE_ARN'].replace(':stateMachine:',':execution:')+':'+execution_name(op)
    try:return boto3.client('stepfunctions').describe_execution(executionArn=arn)['status'] in {'SUCCEEDED','FAILED','TIMED_OUT','ABORTED'}
    except ClientError as exc:
        if exc.response['Error']['Code']=='ExecutionDoesNotExist':return True
        raise

def expiry_handler(event, context=None):
    service = build_service()
    op = service.expire(event["environment_id"])
    if op:
        dispatch(op["operation_id"])
    return {"accepted": bool(op)}

def sweep_handler(event, context=None):
    service, driver = build_service(), build_driver()
    errors = []
    for env in service.store.scan_kind("environment"):
        try:
            service.remind(env['environment_id'])
            env=service.store.get(env['pk'])
            if env["status"] == "READY" and env["deadline"] <= service.clock():
                op = service.expire(env["environment_id"])
                if op:
                    dispatch(op["operation_id"])
            elif env["status"] == "READY" and env.get("schedule_pending"):
                driver.schedule(env)
                env["schedule_pending"] = False
                service.save(env)
        except Exception as exc:
            errors.append({"environment_id": env["environment_id"], "error": type(exc).__name__})
    for op in service.store.scan_kind("operation"):
        try:
            if op["status"] == "QUEUED":
                dispatch(op["operation_id"])
            if op["status"] in {"QUEUED", "RUNNING"}:
                import boto3
                execution_arn = os.environ["STATE_MACHINE_ARN"].replace(":stateMachine:", ":execution:") + ":" + execution_name(op)
                result = boto3.client("stepfunctions").describe_execution(executionArn=execution_arn)
                if result["status"] in {"FAILED", "TIMED_OUT", "ABORTED", "SUCCEEDED"}:
                    current = service.store.get(op["pk"])
                    if current["status"] in {"QUEUED", "RUNNING"}:
                        from .worker import Worker
                        Worker(service, driver).fail(op["operation_id"], "WORKFLOW_" + result["status"],op.get('generation',0))
        except Exception as exc:
            errors.append({"operation_id": op["operation_id"], "error": type(exc).__name__})
    for rp in service.store.scan_kind("recovery"):
        try:
            expirable = {'AVAILABLE','EXPIRING','FAILED'}
            if rp["status"] in expirable and rp["retain_until"] <= service.clock() and not (rp.get("restore_lock") or rp.get('export_lock') or rp.get('delete_lock')):
                # Fence restore against retention expiry before touching AWS.
                rp["status"] = "EXPIRING"
                service.save(rp)
                deleted = driver.expire_backup(rp)
                if deleted:
                    rp = service.store.get(rp["pk"])
                    rp["status"] = "EXPIRED"
                    service.save(rp)
        except Exception as exc:
            errors.append({"recovery_point": rp["recovery_point"], "error": type(exc).__name__})
    if errors:
        print(json.dumps({"event": "CloudHubSweepFailed", "errors": errors}))
        raise RuntimeError("Sweep incomplete; failed items will be retried")
    return {"status": "ok"}
