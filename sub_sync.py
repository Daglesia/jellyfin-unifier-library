import autosubsync
import glob, os
import pkg_resources

input_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'input')

print(f"Looking in: {input_dir}")
print(f"Directory exists: {os.path.exists(input_dir)}")
print(f"Contents: {os.listdir(input_dir)}")

for video_file in glob.glob(os.path.join(input_dir, '*.mp4')):
    base = video_file.rpartition('.')[0]
    srt_file = base + '.srt'
    synced_srt_file = base + '_synced.srt'
    
    if not os.path.exists(srt_file):
        print(f"Skipping {video_file}: no matching .srt file found")
        continue
    
    print(f"Syncing: {os.path.basename(video_file)}")
    autosubsync.synchronize(video_file, srt_file, synced_srt_file)

print("Done.")