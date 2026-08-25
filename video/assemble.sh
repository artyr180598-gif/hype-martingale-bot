#!/bin/bash
set -e
cd "$(dirname "$0")/.."
FF=/usr/local/lib/python3.11/dist-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
BASE=video

dur_of() {
  $FF -i "$1" 2>&1 | grep -o "Duration: [0-9:.]*" | awk -F'[:,.]' '{d=$2*3600+$3*60+$4+$5/100; printf "%.2f", d}' | head -1
}

for i in 1 2 3 4 5 6; do
  dur=$(dur_of $BASE/audio/seg$i.mp3)
  pad=$(python3 -c "print(f'{$dur+0.6:.3f}')")
  echo "--- scene $i: audio ${dur}s, clip ${pad}s"
    $FF -y -loglevel error \
    -loop 1 -framerate 30 -t $pad -i $BASE/img/scene$i.png \
    -i $BASE/audio/seg$i.mp3 \
    -loop 1 -framerate 30 -t $pad -i $BASE/ovl/ovl$i.png \
    -filter_complex "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='1.0+0.0009*in':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=30[bg];[bg][2:v]overlay=0:0,fade=t=in:st=0:d=0.25[v];[1:a]afade=t=in:st=0:d=0.12,apad[a]" \
    -map "[v]" -map "[a]" -t $pad -r 30 \
    -c:v libx264 -preset veryfast -crf 21 -pix_fmt yuv420p \
    -c:a aac -b:a 160k -ar 44100 \
    $BASE/out/scene$i.mp4
done

for i in 1 2 3 4 5 6; do echo "file '$(pwd)/$BASE/out/scene$i.mp4'"; done > $BASE/out/list.txt

$FF -y -loglevel error -f concat -safe 0 -i $BASE/out/list.txt \
  -c:v libx264 -preset veryfast -crf 21 -pix_fmt yuv420p \
  -c:a aac -b:a 160k -movflags +faststart \
  $BASE/facts_tiktok.mp4

echo "=== DONE ==="
$FF -i $BASE/facts_tiktok.mp4 2>&1 | grep -E "Duration|Stream"
ls -la $BASE/facts_tiktok.mp4
