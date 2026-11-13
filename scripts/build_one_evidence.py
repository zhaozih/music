#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Actions: make ONE source MP4 + pre-sanitized evidence review artifact.
No public Releases, no copying any unapproved files. Not an AI semantic analysis.
"""
import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ALLOWED_COLUMNS=['sample_id','text','comment_likes','reply_count','comment_date','redaction_flags']
ALLOWED_FILES=('评论_匿名化.csv','互动数据_公开版.json','脱敏检查报告.json')


def run(*command,stdout=None,stderr=None):
    return subprocess.run(command,check=True,stdout=stdout,stderr=stderr,text=False)


def inside(path,root):
    return path.resolve().is_relative_to(root.resolve())


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def fonts():
    for p in ['/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if Path(p).exists():return ImageFont.truetype(p,17)
    return ImageFont.load_default()


def contact_sheet(images,seconds,indices,output,portrait):
    tw,th=(320,490) if portrait else (460,300)
    cols=3;rows=math.ceil(len(indices)/cols)
    im=Image.new('RGB',(tw*cols,th*rows),(18,20,25))
    draw=ImageDraw.Draw(im)
    f=fonts()
    for order,idx in enumerate(indices):
        path=images[idx]
        x=(order%cols)*tw;y=(order//cols)*th
        with Image.open(path) as frame:
            small=ImageOps.contain(frame.convert('RGB'),(tw-10,th-38))
        im.paste(small,(x+(tw-small.width)//2,y+5))
        draw.text((x+6,y+th-28),f'F{idx:05d}  {seconds[idx]:.3f}s',font=f,fill='white')
    output.parent.mkdir(parents=True,exist_ok=True)
    im.save(output,quality=85)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--work-dir',required=True,help='relative path under dy/reference')
    ap.add_argument('--root',default='.')
    ap.add_argument('--frame-mode',choices=['5','10','original'],default='5')
    ap.add_argument('--out',default='evidence-output')
    args=ap.parse_args()
    repo=Path(args.root).resolve();wd=(repo/args.work_dir).resolve();base=(repo/'dy/reference').resolve()
    if not inside(wd,base) or wd==base or not wd.is_dir():
        raise SystemExit('work_dir must exist under dy/reference, use a per-work folder')
    if wd.is_symlink():raise SystemExit('work_dir symlinks not accepted')
    video_dir=wd/'视频'
    videos=[p for p in video_dir.glob('*') if p.is_file() and p.suffix.lower()=='.mp4' and not p.is_symlink()] if video_dir.exists() else []
    if len(videos)!=1:raise SystemExit(f'Exactly one MP4 required inside {video_dir}; found {len(videos)}')
    video=videos[0]
    if not inside(video,wd):raise SystemExit('Video outside work directory')
    with video.open('rb') as f:
        if f.read(80).startswith(b'version https://git-lfs.github.com/spec/v1'):
            raise SystemExit('Git LFS pointer downloaded instead of video')
    raw_info_path=wd/'作品信息_公开版.json'
    safe_dir=wd/'分析数据_公开版'
    if not raw_info_path.exists() or not safe_dir.is_dir():
        raise SystemExit('Run export_public_reference.py locally first: missing public data files')
    public_info=json.loads(raw_info_path.read_text(encoding='utf-8'))
    if public_info.get('schema_version')!='public_reference_v1':raise SystemExit('Unexpected public manifest schema')
    if not str(public_info.get('work_id','')).isdigit():raise SystemExit('Invalid work ID')
    # Hard whitelist: never package source's raw data, UID/CID, Cookie, settings or history.
    inp=safe_dir/'评论_匿名化.csv'
    with inp.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=ALLOWED_COLUMNS:
            raise SystemExit(f'Unexpected comment columns: {reader.fieldnames}')
        rows=list(reader)
        for row in rows:
            if (re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',row.get('text',''))
                 or re.search(r'(?<!\d)1[3-9]\d{9}(?!\d)',row.get('text',''))
                 or re.search(r'https?://',row.get('text',''))):
                raise SystemExit('Potential personal contact/link in public comments: review locally before packaging')
        sample_count=len(rows)
    for name in ALLOWED_FILES:
        if not (safe_dir/name).is_file():raise SystemExit(f'Missing {name}')
    output=(repo/args.out).resolve()
    if not inside(output,repo) or output==repo or output==wd or output.is_relative_to(wd):
        raise SystemExit('Unsafe output directory')
    output.mkdir(parents=True,exist_ok=True)
    frame_dir=output/'frames';frame_dir.mkdir(exist_ok=True)
    sheet_dir=output/'contact_sheets';sheet_dir.mkdir(exist_ok=True)
    evidence_dir=output/'public_comments';evidence_dir.mkdir(exist_ok=True)
    info_out=output/'作品信息_公开版.json'
    # Re-serialize allowlist instead of copying unreviewed JSON fields.
    allowed_info={k:public_info.get(k) for k in ('schema_version','work_id','work_title','author_display_name','source_url','collection_timestamp_utc','metrics_as_reported','video_analysis_status','nested_reply_text_collected','comment_sample_is_complete')}
    info_out.write_text(json.dumps(allowed_info,ensure_ascii=False,indent=2),encoding='utf-8')
    # Only copy the approved CSV; re-serialize supporting JSON with approved keys.
    shutil.copy2(inp,evidence_dir/'评论_匿名化.csv')
    numeric_keys=('sample_count','comments_with_likes','comments_with_replies','top_comment_likes','top_comment_replies')
    stats_src=json.loads((safe_dir/'互动数据_公开版.json').read_text(encoding='utf-8'))
    stats_out={k:stats_src.get(k) for k in numeric_keys}
    stats_out['metrics_as_reported']={k:(stats_src.get('metrics_as_reported') or {}).get(k) for k in ('views','likes','comments','favorites','shares')}
    stats_out['caution']='Interactive evidence; not verified complete and not causal analysis'
    (evidence_dir/'互动数据_公开版.json').write_text(json.dumps(stats_out,ensure_ascii=False,indent=2),encoding='utf-8')
    report_src=json.loads((safe_dir/'脱敏检查报告.json').read_text(encoding='utf-8'))
    report_out={k:report_src.get(k) for k in ('generated_utc','origin','policy','comment_count','redaction_triggered_rows','comment_sources_count','videos_copied','manual_review_required','warning')}
    (evidence_dir/'脱敏检查报告.json').write_text(json.dumps(report_out,ensure_ascii=False,indent=2),encoding='utf-8')
    with (output/'metadata.json').open('wb') as out:
        run('ffprobe','-v','error','-show_entries','format=duration,size,format_name:stream=index,codec_type,codec_name,width,height,avg_frame_rate','-of','json',str(video),stdout=out)
    metadata=json.loads((output/'metadata.json').read_text())
    v=next((s for s in metadata.get('streams',[]) if s.get('codec_type')=='video'),None)
    if not v:raise SystemExit('No video stream')
    video_filter='showinfo' if args.frame_mode=='original' else f'fps={args.frame_mode},scale=960:-2,showinfo'
    log_path=output/'ffmpeg-frames.log'
    with log_path.open('wb') as e:
        run('ffmpeg','-y','-hide_banner','-nostdin','-nostats','-loglevel','info','-i',str(video),'-map','0:v:0','-vf',video_filter,'-fps_mode','passthrough','-start_number','0','-q:v','3',str(frame_dir/'frame_%06d.jpg'),stderr=e)
    frames=sorted(frame_dir.glob('frame_*.jpg'))
    if not frames or len(frames)>12000:raise SystemExit(f'Invalid frame count: {len(frames)}')
    pattern=re.compile(r'\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*([-+\d.eE]+)')
    pts={}
    for line in log_path.read_text(errors='replace').splitlines():
        m=pattern.search(line)
        if m:
            i=int(m.group(1))
            if i in pts:raise SystemExit('Duplicated PTS')
            pts[i]=float(m.group(3))
    if set(pts)!=set(range(len(frames))):raise SystemExit(f'PTS/frame mismatch {len(pts)} / {len(frames)}')
    times=[max(0,pts[i]-pts[0]) for i in range(len(frames))]
    with (output/'frame-index.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f)
        w.writerow(['frame_file','frame_number_zero_based','relative_seconds','filter_pts_seconds'])
        for i,p in enumerate(frames):w.writerow([p.name,i,round(times[i],6),pts[i]])
    portrait=v['height']>v['width'];chunk=9
    for j in range(0,len(frames),chunk):
        contact_sheet(frames,times,list(range(j,min(j+chunk,len(frames)))),sheet_dir/f'sheet_{j//chunk+1:04d}.jpg',portrait)
    key_indices=[0] if len(frames)==1 else sorted(set(round(i*(len(frames)-1)/8) for i in range(9)))
    contact_sheet(frames,times,key_indices,output/'overview.jpg',portrait)
    audios=[s for s in metadata['streams'] if s.get('codec_type')=='audio']
    if audios:
        run('ffmpeg','-y','-hide_banner','-nostdin','-loglevel','error','-i',str(video),'-map','0:a:0','-vn','-c:a','aac','-b:a','160k',str(output/'audio.m4a'))
    manifest={
        'status':'evidence_not_ai_analysis',
        'work_id':public_info['work_id'],
        'source_video':video.relative_to(repo).as_posix(),
        'source_video_sha256':digest(video),
        'video_bytes':video.stat().st_size,
        'frame_mode':args.frame_mode,'frame_count':len(frames),
        'sampled_comment_count':sample_count,
        'comments_are_sanitized_for_basic_identifiers':True,
        'privacy_notice':'Manual review still required for free text, video, voice and copyright; do not assume full anonymity',
        'completeness_notice':'Collected comments may be partial. No nested replies text. No automatic causal/semantic judgment.',
        'files':{'overview':'overview.jpg','frames':'frames/','sheets':'contact_sheets/','frame_index':'frame-index.csv','audio':'audio.m4a if present','comments':'public_comments/评论_匿名化.csv','metadata':'作品信息_公开版.json'},
    }
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    # FFmpeg logs not needed by users and may contain original paths; delete after indexing.
    log_path.unlink(missing_ok=True)
    (output/'README.txt').write_text('ONE work, one evidence artifact: media frames + audio + public data.\nThis is NOT an AI analysis.\nDo not make public without reviewing copyright/privacy risks.\n',encoding='utf-8')
    print(json.dumps({'work_id':manifest['work_id'],'frames':len(frames),'comments':sample_count,'sha256':manifest['source_video_sha256']},ensure_ascii=False))

if __name__=='__main__':main()
