#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Actions: build one video evidence bundle from either public or raw layout.
Raw collector data is sanitized into a runner-only temporary directory.
No public Release; the artifact still requires privacy/copyright review.
"""
import argparse
import atexit
import tempfile
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


def resolve_work_dir(repo, base, raw):
    """Require a real per-work directory; auto-complete only a unique missing title suffix."""
    requested = Path(raw)
    if requested.is_absolute() or '..' in requested.parts:
        raise SystemExit(f'Invalid relative work_dir: {raw!r}')
    wd = (repo / requested).resolve()
    if not inside(wd,base) or wd == base:
        raise SystemExit('work_dir must be a child of dy/reference')
    if wd.is_dir():
        return wd
    # The V1 workflow shipped a truncated default omitting hashtags.  If exactly
    # one existing title starts with the given string, resolve it safely.
    if wd.parent.is_dir() and inside(wd.parent,base) and wd.parent != base:
        matches = sorted([p for p in wd.parent.iterdir()
                          if p.is_dir() and not p.is_symlink() and p.name.startswith(wd.name)])
        if len(matches) == 1:
            print(f'[WORK_DIR] Completed unique folder prefix: {matches[0].relative_to(repo)}',flush=True)
            return matches[0]
        if len(matches)>1:
            opts='\n'.join('  '+p.relative_to(repo).as_posix() for p in matches)
            raise SystemExit('Ambiguous work_dir; enter exact title folder:\n'+opts)
    available=[]
    if wd.parent.is_dir():
        available=['  '+p.name for p in sorted(wd.parent.iterdir()) if p.is_dir()]
    raise SystemExit(f'Work folder not found: {raw}\n' +
                     ('Available folders:\n'+'\n'.join(available) if available else
                      'Check dy/reference/<account>/<exact title folder>.'))


def normalize_title(text):
    # Filename and exported folder title may have different whitespace/hashtags.
    import unicodedata
    text=unicodedata.normalize('NFKC',str(text or '')).casefold()
    return re.sub(r'[^\w]+','',text,flags=re.UNICODE).replace('_','')


def select_video(repo, base, wd, public_info, video_arg=''):
    """Safe unique matching: work/video first, then account-root legacy MP4s."""
    def safe_mp4_files(directory):
        if not directory.is_dir():return []
        return sorted(p for p in directory.iterdir()
                      if p.is_file() and p.suffix.lower()=='.mp4'
                      and not p.is_symlink() and inside(p,base) and p.stat().st_size>0)
    if video_arg:
        relative=Path(video_arg)
        if relative.is_absolute() or '..' in relative.parts:raise SystemExit('--video-path must be repository-relative')
        video=(repo/relative).resolve()
        if not inside(video,base) or not video.is_file() or video.suffix.lower()!='.mp4' or video.is_symlink():
            raise SystemExit(f'Invalid --video-path: {video_arg}')
        # Explicit path must be in same account, not a different account.
        if not inside(video,wd.parent):raise SystemExit('Video is outside the target account folder')
        return video
    own=safe_mp4_files(wd/'视频')
    if len(own)==1:return own[0]
    if len(own)>1:
        raise SystemExit('Multiple MP4s in work/视频; pass --video-path explicitly')
    parent=safe_mp4_files(wd.parent)
    title=str(public_info.get('work_title') or wd.name).split('#',1)[0].strip()
    clean=normalize_title(title)
    if len(clean)<4:
        raise SystemExit('Work title is too short to safely auto-match an account-root MP4; use --video-path')
    # Downloader may truncate long filenames: a strong normalized title prefix
    # identifies candidate(s) within the already-selected account directory.
    prefix=clean[:min(len(clean),12)]
    matches=[p for p in parent if prefix in normalize_title(p.stem)]
    if len(matches)==1:
        print('[VIDEO] Matched legacy MP4 in account directory by title prefix',flush=True)
        return matches[0]
    if not matches:
        print('[VIDEO] Account-root MP4 candidates: '+(', '.join(p.name for p in parent) or '(none)'),flush=True)
        raise SystemExit(f'No matching MP4 for work title {title!r}; add 视频/<file>.mp4 or pass --video-path')
    raise SystemExit(f'Ambiguous account-root MP4 matches for {title!r}: '+
                     ', '.join(p.name for p in matches)+'; pass --video-path')


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
    ap.add_argument('--work-dir',required=True,help='relative per-work folder inside dy/reference')
    ap.add_argument('--root',default='.')
    ap.add_argument('--frame-mode',choices=['5','10','original'],default='5')
    ap.add_argument('--video-path',default='',help='optional explicit repository-relative MP4 for ambiguous title matches')
    ap.add_argument('--out',default='evidence-output')
    args=ap.parse_args()
    repo=Path(args.root).resolve();base=(repo/'dy/reference').resolve()
    wd=resolve_work_dir(repo,base,args.work_dir)
    if wd.is_symlink():raise SystemExit('work_dir symlinks not accepted')
    raw_info_path=wd/'作品信息_公开版.json'
    safe_dir=wd/'分析数据_公开版'
    public_files_ready = raw_info_path.is_file() and safe_dir.is_dir() and all(
        (safe_dir / name).is_file() for name in ALLOWED_FILES
    )
    if public_files_ready:
        evidence_source = 'pre_sanitized_public'
        print('[DATA] Found complete public input; using existing anonymized CSV', flush=True)
    elif (wd/'作品信息.json').is_file() and (
        (wd/'原始数据').is_dir() or (wd/'分析数据').is_dir()
    ):
        # Do not copy raw CSV/ZIP to final artifact or back to GitHub.
        # Imported from the adjacent V1.4 exporter to preserve comment metrics
        # 点赞数量 / 回复数量 (and previous aliases) and basic PII masking.
        try:
            from export_public_reference import export_work
        except ImportError as exc:
            raise SystemExit('Missing scripts/export_public_reference.py (V1.4); upload both patch files') from exc
        staging = tempfile.TemporaryDirectory(prefix='reference_public_stage_')
        atexit.register(staging.cleanup)
        result = export_work(wd, Path(staging.name), include_video=False)
        stage_work = Path(staging.name) / result['work_folder']
        raw_info_path = stage_work / '作品信息_公开版.json'
        safe_dir = stage_work / '分析数据_公开版'
        if not raw_info_path.is_file() or not all(
            (safe_dir / name).is_file() for name in ALLOWED_FILES
        ):
            raise SystemExit('Auto-sanitizing raw collector data failed: public outputs incomplete')
        evidence_source = 'auto_sanitized_raw'
        print(f'[DATA] Auto-sanitized raw collector data in temporary staging; '
              f"status={result['status']}, comments={result['comments_exported']}", flush=True)
        if not result['comments_exported']:
            raise SystemExit('No usable comment text found in raw/analysis CSV; refused empty evidence')
    else:
        raise SystemExit('Missing both supported input layouts: '
                         'expected 作品信息_公开版.json + 分析数据_公开版/ OR '
                         '作品信息.json + 原始数据/ + 分析数据/')
    public_info=json.loads(raw_info_path.read_text(encoding='utf-8'))
    if public_info.get('schema_version')!='public_reference_v1':raise SystemExit('Unexpected public manifest schema')
    if not str(public_info.get('work_id','')).isdigit():raise SystemExit('Invalid work ID')
    video=select_video(repo,base,wd,public_info,args.video_path)
    print(f'[VIDEO] Selected: {video.relative_to(repo).as_posix()}',flush=True)
    with video.open('rb') as f:
        if f.read(80).startswith(b'version https://git-lfs.github.com/spec/v1'):
            raise SystemExit('Git LFS pointer downloaded instead of video')
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
    if not inside(output,repo) or output==repo or output==wd or output.is_relative_to(wd) or wd.is_relative_to(output):
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
    numeric_keys=('sample_count','comments_with_likes','comments_with_replies','top_comment_likes','top_comment_replies','comment_likes_status','comment_replies_status','comment_likes_true_zero_rows','comment_replies_true_zero_rows')
    stats_src=json.loads((safe_dir/'互动数据_公开版.json').read_text(encoding='utf-8'))
    stats_out={k:stats_src.get(k) for k in numeric_keys}
    stats_out['metrics_as_reported']={k:(stats_src.get('metrics_as_reported') or {}).get(k) for k in ('views','likes','comments','favorites','shares')}
    stats_out['caution']='Interactive evidence; not verified complete and not causal analysis'
    (evidence_dir/'互动数据_公开版.json').write_text(json.dumps(stats_out,ensure_ascii=False,indent=2),encoding='utf-8')
    def known_metric_count(field):
        val=stats_out.get(field)
        if isinstance(val,int) and 0 <= val <= sample_count:
            return val
        return None
    likes_known=known_metric_count('comments_with_likes')
    replies_known=known_metric_count('comments_with_replies')
    data_quality={
        'input_layout':evidence_source,
        'comment_sample_rows':sample_count,
        'comment_likes_known':likes_known,
        'comment_likes_missing':sample_count-likes_known if likes_known is not None else None,
        'comment_replies_known':replies_known,
        'comment_replies_missing':sample_count-replies_known if replies_known is not None else None,
        'source_warning':'Comment data are collected samples, not randomized viewer responses; audience opinion proportions cannot be inferred.',
        'engagement_warning':'Reply totals are not reply texts; high reply count does not prove disagreement.',
        'platform_warning':'Views/completion/retention metrics are not inferred or fabricated.',
        'privacy_warning':'Text, audio and video may contain personal information; basic masking does not guarantee anonymity.'
    }
    (evidence_dir/'数据质量.json').write_text(json.dumps(data_quality,ensure_ascii=False,indent=2),encoding='utf-8')
    # Optional metric-gap diagnostic from V1.4 exporter. Copy only known aggregate
    # values, never filenames, raw schema samples, CIDs, nicknames or free text.
    diag_path=safe_dir/'评论字段诊断_公开版.json'
    metric_gap=None
    if diag_path.is_file():
        diag=json.loads(diag_path.read_text(encoding='utf-8'))
        keys=('comments_exported','comment_likes_known','comment_replies_known',
              'likes_actual_zero_count','replies_actual_zero_count',
              'likes_issue','replies_issue','history_metric_backfills')
        metric_gap={k:diag.get(k) for k in keys}
        (evidence_dir/'评论指标诊断.json').write_text(json.dumps(metric_gap,ensure_ascii=False,indent=2),encoding='utf-8')
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
        'input_layout': evidence_source,
        'work_id':public_info['work_id'],
        'source_video':video.relative_to(repo).as_posix(),
        'source_work_dir':wd.relative_to(repo).as_posix(),
        'source_video_sha256':digest(video),
        'video_bytes':video.stat().st_size,
        'frame_mode':args.frame_mode,'frame_count':len(frames),
        'sampled_comment_count':sample_count,
        'comment_likes_known':likes_known,
        'comment_replies_known':replies_known,
        'comment_metrics_quality':metric_gap if metric_gap is not None else 'no_v1_4_diagnostic_available',
        'comments_are_sanitized_for_basic_identifiers':True,
        'privacy_notice':'Manual review still required for free text, video, voice and copyright; do not assume full anonymity',
        'completeness_notice':'Collected comments may be partial. No nested replies text. No automatic causal/semantic judgment.',
        'files':{'overview':'overview.jpg','frames':'frames/','sheets':'contact_sheets/','frame_index':'frame-index.csv','audio':'audio.m4a if present','comments':'public_comments/评论_匿名化.csv','data_quality':'public_comments/数据质量.json','metadata':'作品信息_公开版.json'},
    }
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    # FFmpeg logs not needed by users and may contain original paths; delete after indexing.
    log_path.unlink(missing_ok=True)
    (output/'README.txt').write_text('ONE work, one evidence artifact: media frames + audio + public data.\nThis is NOT an AI analysis.\nDo not make public without reviewing copyright/privacy risks.\n',encoding='utf-8')
    print(json.dumps({'work_id':manifest['work_id'],'frames':len(frames),'comments':sample_count,'source':evidence_source,'sha256':manifest['source_video_sha256']},ensure_ascii=False))

if __name__=='__main__':main()
