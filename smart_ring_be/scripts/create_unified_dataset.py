import os
import shutil
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parents[1]
PACKAGES_DIR = BASE_DIR / "datasets" / "packages_v4"
COCO_DIR = Path("/Users/admin/personal/ai_image_analyzer/datasets/coco128")
OUTPUT_DIR = BASE_DIR / "datasets" / "unified"

# New Class Mapping
# 0: package
# 1: person
# 2: car
COCO_PERSON = 0
COCO_CAR = 2


def setup_dirs():
    for split in ["train", "val"]:
        (OUTPUT_DIR / "images" / split).mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / "labels" / split).mkdir(parents=True, exist_ok=True)


def copy_package_data():
    print("📦 Processing package dataset...")
    counts = {"train": 0, "val": 0}
    
    mapping = {
        "train": (PACKAGES_DIR / "train"),
        "val": (PACKAGES_DIR / "valid"),
    }
    
    for split, src_dir in mapping.items():
        img_dir = src_dir / "images"
        lbl_dir = src_dir / "labels"
        if not img_dir.exists():
            continue
            
        for img_path in img_dir.glob("*.*"):
            if img_path.suffix.lower() not in [".jpg", ".jpeg", ".png"]:
                continue
            lbl_path = lbl_dir / f"{img_path.stem}.txt"
            if not lbl_path.exists():
                continue
            
            # Copy image
            dest_img = OUTPUT_DIR / "images" / split / f"pkg_{img_path.name}"
            shutil.copy2(img_path, dest_img)
            
            # Label class is already 0 (package)
            dest_lbl = OUTPUT_DIR / "labels" / split / f"pkg_{img_path.stem}.txt"
            shutil.copy2(lbl_path, dest_lbl)
            counts[split] += 1
            
    print(f"   Copied packages: {counts['train']} train, {counts['val']} val")


def copy_coco_person_car():
    print("🚗 Processing COCO person & car samples...")
    coco_imgs = COCO_DIR / "images" / "train2017"
    coco_lbls = COCO_DIR / "labels" / "train2017"
    
    if not coco_imgs.exists():
        print(f"   COCO images not found at {coco_imgs}")
        return

    img_files = list(coco_imgs.glob("*.*"))
    person_car_samples = []

    for img_path in img_files:
        lbl_path = coco_lbls / f"{img_path.stem}.txt"
        if not lbl_path.exists():
            continue

        with open(lbl_path, "r") as f:
            lines = [line.strip().split() for line in f if line.strip()]

        # Filter for person (0) and car (2)
        valid_boxes = []
        for parts in lines:
            if not parts:
                continue
            cls_id = int(parts[0])
            if cls_id == COCO_PERSON:
                # Map person to class 1
                valid_boxes.append(f"1 {' '.join(parts[1:])}")
            elif cls_id == COCO_CAR:
                # Map car to class 2
                valid_boxes.append(f"2 {' '.join(parts[1:])}")

        if valid_boxes:
            person_car_samples.append((img_path, valid_boxes))

    # Split 80% train, 20% val
    num_samples = len(person_car_samples)
    split_idx = int(num_samples * 0.8)
    
    counts = {"train": 0, "val": 0}
    for idx, (img_path, boxes) in enumerate(person_car_samples):
        split = "train" if idx < split_idx else "val"
        dest_img = OUTPUT_DIR / "images" / split / f"coco_{img_path.name}"
        dest_lbl = OUTPUT_DIR / "labels" / split / f"coco_{img_path.stem}.txt"
        
        shutil.copy2(img_path, dest_img)
        with open(dest_lbl, "w") as f:
            f.write("\n".join(boxes) + "\n")
        counts[split] += 1

    print(f"   Copied COCO samples: {counts['train']} train, {counts['val']} val")


def write_yaml():
    yaml_content = f"""path: {OUTPUT_DIR.resolve()}
train: images/train
val: images/val

names:
  0: package
  1: person
  2: car

nc: 3
"""
    with open(OUTPUT_DIR / "data.yaml", "w") as f:
        f.write(yaml_content)
    print(f"✅ Generated data.yaml at {OUTPUT_DIR / 'data.yaml'}")


if __name__ == "__main__":
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    setup_dirs()
    copy_package_data()
    copy_coco_person_car()
    write_yaml()
    print("🎉 Unified dataset creation complete!")
