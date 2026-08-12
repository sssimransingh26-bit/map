"""
Runs the forensic engine over a labeled test set and reports real accuracy/precision/recall/F1 numbers
Usage: python evaluate.py --data_dir test_data --threshold 
use the folder named test_data as where the images are(default test_data)
use 30 as the cutoff score — anything scoring 30 or above gets called suspicious
"""

import argparse
import glob
import os
import exifread
from forensics import run_full_analysis


def load_tags(filepath: str) -> dict:
    with open(filepath, "rb") as f:
        return exifread.process_file(f, details=False)


def evaluate(data_dir: str, threshold: float):#setting up folders
    authentic_dir = os.path.join(data_dir, "authentic")
    suspicious_dir = os.path.join(data_dir, "sus")

    results = []  # (true_label, predicted_score, predicted_label, filename)

    for label, folder in [("authentic", authentic_dir), ("suspicious", suspicious_dir)]:
        paths = sorted(
            glob.glob(os.path.join(folder, "*.jpg")) +
            glob.glob(os.path.join(folder, "*.jpeg")) +
            glob.glob(os.path.join(folder, "*.png"))
        )
        if not paths:
            print(f"WARNING: no images found in {folder}")
        for path in paths:#analyzing each image
            tags = load_tags(path)#gets exif metadata
            report = run_full_analysis(path, tags)
            for sig in report.signals:
                print(f"    [{label}] {sig.name}: score={sig.score:.2f}")
            score = report.authenticity_score#0-100 fused score
            predicted = "suspicious" if score >= threshold else "authentic"
            results.append((label, score, predicted, os.path.basename(path)))

#results entry is (true_label, score, predicted_label, filename)
#The t, _, p, _ pattern unpacks that tuple — t = true label, p = predicted label
#score and filename ignored here
    tp = sum(1 for t, _, p, _ in results if t == "suspicious" and p == "suspicious")
    fn = sum(1 for t, _, p, _ in results if t == "suspicious" and p == "authentic")
    fp = sum(1 for t, _, p, _ in results if t == "authentic" and p == "suspicious")
    tn = sum(1 for t, _, p, _ in results if t == "authentic" and p == "authentic")

    total = len(results)
    accuracy = (tp + tn) / total if total else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    print(f"\n=== TraceLens Evaluation (threshold={threshold}) ===")
    print(f"Total images: {total}")
    print("Confusion matrix:")
    print("                 pred_authentic   pred_suspicious")
    print(f"  true_authentic       {tn:>6}           {fp:>6}")#{tn:>6} means "print this number right-aligned in a 6-character-wide space," purely for neat column alignment.
    print(f"  true_suspicious      {fn:>6}           {tp:>6}")
    print(f"\nAccuracy:  {accuracy:.2%}")#:.2% formats a decimal as a percentage with 2 decimal places
    print(f"Precision: {precision:.2%}")
    print(f"Recall:    {recall:.2%}")
    print(f"F1:        {f1:.2%}")

    print("\nMisclassified examples:")
    for t, score, p, name in results:
        if t != p:
            print(f"  [{t} -> predicted {p}] score={score:.1f}  {name}")

    return {"accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data_dir", default="test_data")
    parser.add_argument("--threshold", type=float, default=30.0)
    args = parser.parse_args()
    evaluate(args.data_dir, args.threshold)


"""
tp (true positive) — actually suspicious, correctly caught
fn (false negative) — actually suspicious, but your engine missed it (called it authentic)
fp (false positive) — actually authentic, but your engine wrongly flagged it
tn (true negative) — actually authentic, correctly cleared
"""