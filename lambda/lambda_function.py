import json
import boto3
import urllib.parse
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from botocore.config import Config

TABLE_NAME = 'ProcessedFiles'
S3_REGION = 'ap-south-1'
DYNAMODB_REGION = 'us-east-1'
DEFAULT_BUCKET = 'serverless-pipeline-uploads'

dynamodb = boto3.resource('dynamodb', region_name=DYNAMODB_REGION)
s3_client = boto3.client('s3', region_name=S3_REGION, config=Config(signature_version='s3v4'))
table = dynamodb.Table(TABLE_NAME)

CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Content-Type': 'application/json',
}


def api_response(status_code, body):
    return {
        'statusCode': status_code,
        'headers': CORS_HEADERS,
        'body': json.dumps(body, default=decimal_default),
    }


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError


def handle_s3_event(event):
    record = event['Records'][0]
    bucket = record['s3']['bucket']['name']
    key = urllib.parse.unquote_plus(record['s3']['object']['key'])
    size = record['s3']['object']['size']

    print(f"Processing file: {key} from bucket: {bucket}")

    file_extension = key.rsplit('.', 1)[-1].lower() if '.' in key else 'unknown'
    item = build_item(key, size, file_extension, bucket)

    table.put_item(Item=item)
    print(f"Saved to DynamoDB: {item['fileId']}")

    return {'statusCode': 200, 'body': json.dumps({'message': 'File processed', 'fileId': item['fileId']})}


def build_item(file_name, file_size, file_type, bucket=DEFAULT_BUCKET):
    return {
        'fileId': str(uuid.uuid4()),
        'fileName': file_name,
        'bucket': bucket,
        'fileSize': file_size,
        'fileType': file_type,
        'processedAt': datetime.now(timezone.utc).isoformat(),
        'status': 'SUCCESS',
    }


def handle_presign(body):
    file_name = (body.get('fileName') or '').strip()
    file_type = body.get('fileType') or 'application/octet-stream'
    bucket = (body.get('bucket') or DEFAULT_BUCKET).strip()

    if not file_name:
        return api_response(400, {'error': 'fileName is required'})

    file_key = f"uploads/{uuid.uuid4().hex[:8]}-{file_name}"
    upload_url = s3_client.generate_presigned_url(
        'put_object',
        Params={
            'Bucket': bucket,
            'Key': file_key,
            'ContentType': file_type,
        },
        ExpiresIn=300,
    )

    return api_response(200, {'uploadUrl': upload_url, 'fileKey': file_key})


def handle_api_event(event):
    method = event.get('httpMethod') or event.get('requestContext', {}).get('http', {}).get('method', '')
    path = event.get('path') or event.get('requestContext', {}).get('http', {}).get('path', '')

    if method == 'OPTIONS':
        return api_response(200, {})

    if method == 'POST' and path.endswith('/presign'):
        body = json.loads(event.get('body') or '{}')
        return handle_presign(body)

    if method == 'GET' and path.endswith('/records'):
        response = table.scan()
        items = response.get('Items', [])
        items.sort(key=lambda x: x.get('processedAt', ''), reverse=True)
        return api_response(200, items)

    if method == 'POST' and path.endswith('/process'):
        body = json.loads(event.get('body') or '{}')
        file_name = body.get('fileName', f"test-{uuid.uuid4().hex[:8]}.csv")
        file_size = int(body.get('fileSize', 2048))
        file_type = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else 'csv'

        item = build_item(file_name, file_size, file_type)
        table.put_item(Item=item)
        return api_response(200, {'message': 'Pipeline triggered', 'fileId': item['fileId']})

    return api_response(404, {'error': 'Not found'})


def lambda_handler(event, context):
    try:
        if 'Records' in event:
            return handle_s3_event(event)
        if 'httpMethod' in event or 'requestContext' in event:
            return handle_api_event(event)
        return api_response(400, {'error': 'Unsupported event type'})
    except Exception as e:
        print(f"ERROR: {str(e)}")
        if 'httpMethod' in event or 'requestContext' in event:
            return api_response(500, {'error': str(e)})
        raise
