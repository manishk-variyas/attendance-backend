import aioboto3
from botocore.exceptions import ClientError
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

class StorageService:
    def __init__(self):
        self.session = aioboto3.Session()
        self.bucket_name = settings.MINIO_BUCKET_NAME
        self.endpoint_url = settings.MINIO_URL
        self.access_key = settings.MINIO_ACCESS_KEY
        self.secret_key = settings.MINIO_SECRET_KEY
        self.region = settings.MINIO_REGION

    async def _get_client(self):
        return self.session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
        )

    async def ensure_bucket_exists(self, bucket_name=None):
        bucket = bucket_name or self.bucket_name
        async with await self._get_client() as s3:
            try:
                await s3.head_bucket(Bucket=bucket)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code")
                if error_code == "404":
                    logger.info(f"Creating bucket {bucket}")
                    await s3.create_bucket(Bucket=bucket)
                else:
                    logger.error(f"Error checking bucket: {e}")
                    raise

    async def upload_file(self, file_content, object_name, bucket_name=None, content_type="application/octet-stream"):
        bucket = bucket_name or self.bucket_name
        await self.ensure_bucket_exists(bucket)
        async with await self._get_client() as s3:
            await s3.put_object(
                Bucket=bucket,
                Key=object_name,
                Body=file_content,
                ContentType=content_type
            )
            return f"{self.endpoint_url}/{bucket}/{object_name}"

    async def delete_file(self, object_name):
        async with await self._get_client() as s3:
            await s3.delete_object(Bucket=self.bucket_name, Key=object_name)

    async def get_file(self, object_name, bucket_name=None):
        """Retrieve a file's content and content type from MinIO."""
        bucket = bucket_name or self.bucket_name
        async with await self._get_client() as s3:
            response = await s3.get_object(Bucket=bucket, Key=object_name)
            content = await response["Body"].read()
            content_type = response.get("ContentType", "application/octet-stream")
            return content, content_type

    async def generate_presigned_url(self, object_name, expires_in=3600):
        """
        Generate a presigned URL to share the object.
        Note: If MinIO is internal only, this URL will only work within the network
        or if proxied correctly.
        """
        async with await self._get_client() as s3:
            return await s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': object_name},
                ExpiresIn=expires_in
            )

storage_service = StorageService()
