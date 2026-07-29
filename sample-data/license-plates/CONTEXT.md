# License Plate Detection

**Source:** [HuggingFace - keremberke/license-plate-object-detection](https://huggingface.co/datasets/keremberke/license-plate-object-detection)
**Original:** [Roboflow Universe - Augmented Startups / Vehicle Registration Plates](https://universe.roboflow.com/augmented-startups/vehicle-registration-plates-trudk)
**License:** CC BY 4.0 (per Roboflow / Augmented Startups)
**Task:** Object Detection (single class: `license_plate`)

## Overview

Real-world vehicle photos with bounding-box annotations around the
license plate. One annotation per visible plate, in COCO format. Useful
for testing the rich-logging dashboard against a CV workload — the
agent can stream prediction-overlay images, an IoU/confidence histogram,
and a per-confidence-bucket confusion-style breakdown live to the
Metrics tab while training.

## Splits shipped

| Split | Images | Annotations | Disk | Use it for |
|-------|--------|-------------|------|------------|
| `valid-mini/` | 3 | 3 | 80 KB | Smoke test — load + visualize one batch end-to-end without training |
| `test/` | 882 | 902 | 23 MB | Realistic eval set — small enough to upload via UI, big enough to evaluate a detector |
| `valid/` | 1,765 | 1,840 | 48 MB | Larger eval / second pass; can be split into train+val by the agent |

The full training split (6,176 images, 163 MB) is NOT shipped — it's
overkill for a CV demo and slow to upload through the browser. Run
`python download.py --split train` if you want it.

## Annotation format

Each split folder is self-contained:

```
test/
├── _annotations.coco.json      # COCO-format, single category id=0 ("license_plate")
├── img_001.jpg
├── img_002.jpg
└── ...
```

Every COCO annotation row looks like:

```json
{
  "id": 234,
  "image_id": 223,
  "category_id": 0,
  "bbox": [249, 186, 101, 49],   // [x, y, w, h] in pixels
  "area": 4949,
  "segmentation": [],
  "iscrowd": 0
}
```

## Why this dataset

- Single class (`license_plate`) — keeps the modeling story simple
- Wide variety of vehicles, angles, lighting, and plate formats (US + EU)
- Image sizes vary (216×160 → 4653×2910) — exercises any resize/letterbox
  logic the agent writes
- Good fit for testing **rich logging**:
  - `trainable.log_images(step=epoch, key="val/predictions", images=[...])`
    → grid of prediction overlays per epoch
  - `trainable.log_table(step=epoch, key="val/worst", columns=["img","gt","pred","iou","conf"], rows=[...])`
    → top-K errors as a table
  - `trainable.log_figure(step=N, key="val/iou_hist", fig=plt.gcf())`
    → IoU distribution across the eval set
  - `trainable.log_confusion_matrix(...)` is less natural for detection
    (no fixed class set), but the agent can derive a confidence-bin
    confusion (low-conf vs missed vs hit) if they want to demo it

## Suggested approaches

For a fast end-to-end run on this dataset:

1. **Pretrained YOLOv8n + light fine-tune** — fastest path to working
   predictions; ultralytics ships a CLI that handles COCO directly.
   Log a per-epoch `image_grid` of predictions on a fixed val batch.
2. **Faster R-CNN from torchvision (`fasterrcnn_resnet50_fpn`)** —
   more bytes to ship to the dashboard but exercises the full PyTorch
   training loop where `trainable.log()` per-step matters.

The agent doesn't have to hit SOTA mAP; the goal here is to surface a
streamed visual training story in the Metrics tab.

## Re-downloading / refreshing

The splits live behind `download.py`:

```bash
# Re-download everything except train (default)
python sample-data/license-plates/download.py

# Add the full train split too (~163 MB)
python sample-data/license-plates/download.py --split train

# Just one split
python sample-data/license-plates/download.py --split valid-mini
```

## Skipping the browser upload

Browser multipart upload of ~2.6k images is slow. Use `upload_to_s3.py`
to push the splits to MinIO at a project-agnostic path:

```bash
backend/.venv/bin/python sample-data/license-plates/upload_to_s3.py

# Or just one split
backend/.venv/bin/python sample-data/license-plates/upload_to_s3.py --split test
```

Files land at `s3://datasets/sample-data/license-plates/{split}/...` in
MinIO. The script doesn't need a project ID — it stages the dataset
once, and you attach it into projects later via the existing UI flow:

1. In a chat, click the attach (**+**) button → **Browse S3**
2. Navigate to `sample-data/license-plates/{split}/`
3. Select the folder → backend syncs MinIO → Modal Volume → agent's
   sandbox sees them at `/data/projects/{pid}/datasets/{split}/...`

Same dataset, multiple projects: rerun step 1–3 in any project.
