"""来源帧的可再生磁盘索引；原文件仍逐次验签，缓存不保存模型判断。"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from runtime import atomic, digest, json_bytes
from source_snapshot import file_sha256


# 小行解析便宜，不为其创建多个磁盘文件；阈值只控制工程缓存，不筛选材料。
MIN_CACHE_BYTES=64*1024


def metadata_sha(value):
    # 元数据不含正文；C编码与digest的字节合同相同，避免逐token Python循环。
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class FrameCache:
    def __init__(self, root):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        # 只缓存协议帧解析；人员、时窗及归属始终由当次prepare重新选择。
        self.parser=digest({name:file_sha256(Path(__file__).with_name(name))
            for name in ('source_frames.py','source_frame_cache.py')})

    def frames(self, source_root, ref, *, include_at, user_id, include_group, metadata_only):
        from source_frames import _row_frames,read_signed
        source=(source_root/ref['path']).resolve()
        if not source.is_relative_to(source_root.resolve()):raise ValueError('来源路径越界')
        if source.stat().st_size<MIN_CACHE_BYTES:
            yield from _row_frames(read_signed(source_root,ref),ref,include_at,user_id,include_group,metadata_only)
            return
        # 命中也不把缓存当原件；回写修订由原SHA自然生成新键。
        if file_sha256(source)!=ref['sha256']:raise ValueError('来源签名变化')
        key=digest(dict(source=ref,parser=self.parser))
        directory=self.root/key[:2]/key
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        proof_path=directory/'index.json'
        with os.fdopen(os.open(directory/'build.lock',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600),'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if not proof_path.exists():
                if not self.build(source_root,ref,directory):
                    # 异常源不能在全量建索引时提前改变原有选日校验语义。
                    from source_frames import read_signed
                    yield from _row_frames(read_signed(source_root,ref),ref,include_at,user_id,include_group,metadata_only)
                    return
            proof=json.loads(proof_path.read_text())
            if (proof.get('source')!=ref or proof.get('parser')!=self.parser
                    or metadata_sha(proof.get('metadata'))!=proof.get('metadata_sha256')):
                raise ValueError('来源帧索引合同或签名变化')
            # 元数据命中也核帧文件；损坏不静默重建或当无活动。
            data=directory/'frames.jsonl'
            if file_sha256(data)!=proof['frames_sha256']:raise ValueError('来源帧缓存签名变化')
        def selected(frame):
            group=frame.get('session_id') or 'audit:'+frame['frame'].split(':')[1]
            return ((user_id is None or frame['user_id']==user_id)
                and (include_group is None or include_group(group))
                and (include_at is None or include_at(frame['at'])))
        if metadata_only:
            for frame,issues in proof['metadata']:
                if selected(frame):yield frame,issues
            return
        # 无关会话只需验签与元数据，不反序列化正文。
        if not any(selected(frame) for frame,_ in proof['metadata']):return
        with data.open() as stream:
            for line in stream:
                frame,issues=json.loads(line)
                if selected(frame):yield frame,issues
                del frame,issues,line

    def build(self, source_root, ref, directory):
        from source_frames import _row_frames
        from disk_guard import require_space
        require_space(directory,0)
        metadata=[]
        fd,name=tempfile.mkstemp(prefix='frames-',suffix='.tmp',dir=directory)
        tmp=Path(name)
        try:
            with os.fdopen(fd,'wb') as stream:
                from source_frames import read_signed
                try:row=read_signed(source_root,ref)
                except (ValueError,KeyError,TypeError):return False
                frames=_row_frames(row,ref,None,None,None,False)
                del row
                while True:
                    # 仅来源解析异常允许回到原选日路径；写入/磁盘暂停必须向上传递。
                    try:frame,issues=next(frames)
                    except StopIteration:break
                    except (ValueError,KeyError,TypeError):return False
                    for chunk in json_bytes([frame,issues],sort_keys=False):
                        require_space(directory,len(chunk)+8192)
                        stream.write(chunk)
                    stream.write(b'\n')
                    metadata.append([{k:v for k,v in frame.items() if k not in {'text','text_sha256'}},issues])
                    del frame
                stream.flush();os.fsync(stream.fileno())
            signature=file_sha256(tmp)
            os.replace(tmp,directory/'frames.jsonl')
            # 完整数据落盘后才封口；失败的部分文件不会成为命中。
            atomic(directory/'index.json',dict(source=ref,parser=self.parser,metadata=metadata,
                metadata_sha256=metadata_sha(metadata),frames_sha256=signature))
            return True
        finally:
            if tmp.exists():tmp.unlink()
