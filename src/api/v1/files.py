import os
from typing import Any
from uuid import UUID
from pathlib import Path
from pydantic import constr
from sqlalchemy.ext.asyncio import AsyncSession
from aiobotocore.session import AioBaseClient
from zipstream import AioZipStream
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from fastapi import (
    APIRouter, Depends, HTTPException, status, Request, Query, Form, UploadFile
)

from core.s3 import get_s3_client
from db.db import get_session
from schemas import file as file_schema
from services.auth.auth_bearer import JWTBearer
from services.auth.auth_handler import get_user_id
from services.exceptions import UploadException, DownloadException
from services.s3_files.upload import upload_content
from services.s3_files.download import download_content
from services.utils import translit
from services.file import (
    add_file_db_record, get_all_files, search_files_in_db, get_file_obj
)


router = APIRouter()


@router.get(
    '/list',
    response_model=file_schema.FileDownloaded,
    summary='Get a list of available files',
    description='Get a list of available files for download.',
    dependencies=[Depends(JWTBearer())]
)
async def get_list_files(
        db: AsyncSession = Depends(get_session),
        user_id: str = Depends(get_user_id),
        skip: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=0)
) -> Any:
    """
    Retrieve list of files.
    """
    files = await get_all_files(db=db, user_id=user_id, skip=skip, limit=limit)

    files_dict = jsonable_encoder(files)
    files = []
    if files_dict:
        files = [file_schema.FileInfo(**file) for file in files_dict]
    result = file_schema.FileDownloaded(
        account_id=user_id,
        files=files
    )
    return result


@router.post(
    '/upload',
    status_code=status.HTTP_201_CREATED,
    response_model=file_schema.FileInDBBase,
    summary='Upload new file',
    description='Upload new file to file storage.',
    dependencies=[Depends(JWTBearer())]
)
async def upload_file(
        *,
        request: Request,
        db: AsyncSession = Depends(get_session),
        user_id: str = Depends(get_user_id),
        path: constr(regex=r'^[^\/].+(?=\/)*[\/]?.+$') = Form(...),
        file_bytes: UploadFile,
        s3_client: AioBaseClient = Depends(get_s3_client)
) -> Any:
    """
    Upload new file.
    """
    if path[-1] == '/':
        path = os.path.join(path, file_bytes.filename)

    object_in = file_schema.FileCreate(
            name=Path(path).name,
            path=path,
            size=request.headers['content-length'],
            is_downloadable=True,
            account_id=user_id,
            content_type=file_bytes.content_type,
            extension=os.path.splitext(Path(path).name)[1].replace('.', '')
    )
    try:
        await upload_content(
            client=s3_client, content=file_bytes.file, file_path=path
        )
    except UploadException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail='S3 upload error'
        )
    file_object = await add_file_db_record(db=db, obj_in=object_in)

    return file_object


@router.get(
    '/download',
    status_code=status.HTTP_200_OK,
    response_class=StreamingResponse,
    summary='Download file',
    description='Download file from file storage by PATH or UUID4.',
    dependencies=[Depends(JWTBearer())]
)
async def download_file(
        *,
        zipped: bool = False,
        db: AsyncSession = Depends(get_session),
        user_id: str = Depends(get_user_id),
        path: constr(regex=r'^[^\/].+(?=\/)*[\/]?.+$') | UUID =
        Query(..., description='Path or UUID4'),
        s3_client: AioBaseClient = Depends(get_s3_client)
) -> Any:
    """
    Download file.
    """
    file_obj = await get_file_obj(db=db, path=path, user_id=user_id)

    if not file_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='File with this UUID or PATH not found'
        )
    if zipped is True:
        file_obj = [file_obj]
    if isinstance(file_obj, list):
        files = [
            {
                'stream': await download_content(
                    client=s3_client, file_path=file.path),
                'name': file.name
            } for file in file_obj
        ]
        content = AioZipStream(files, chunksize=32768).stream()
        media_type = 'application/x-zip-compressed'
        file_name = 'files.zip'
    else:
        content = await download_content(
            client=s3_client, file_path=file_obj.path)
        media_type = file_obj.content_type
        file_name = file_obj.name.translate(translit)
    try:
        return StreamingResponse(
            content,
            media_type=media_type,
            headers={
                'Content-Disposition': f'attachment; filename="{file_name}"'
            }
        )
    except DownloadException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail='S3 download error'
        )


@router.get(
    '/search',
    response_model=file_schema.SearchFile,
    summary='Searching files',
    description='Searching files by different parameters.',
    dependencies=[Depends(JWTBearer())]
)
async def search_files(
        db: AsyncSession = Depends(get_session),
        user_id: str = Depends(get_user_id),
        path: str = None,
        extension: str = None,
        limit: int = Query(default=100, ge=0),
        query: str = '',
        is_regex: bool = Query(default=False, description='query is regex'),
        order_by: str = Query(
            default='id', enum=['id', 'name', 'created_at', 'path', 'size']
        ),
) -> Any:
    """
    Search files.
    """
    files = await search_files_in_db(
        db=db, user_id=user_id, path=path, extension=extension, query=query,
        is_regex=is_regex, order_by=order_by, limit=limit
    )
    files_dict = jsonable_encoder(files)
    files = []
    if files_dict:
        files = [file_schema.FileInfo(**file) for file in files_dict]
    result = file_schema.SearchFile(matches=files)
    return result
