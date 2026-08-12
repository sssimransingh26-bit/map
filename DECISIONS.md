# Design Decisions & Trade-offs

Notes on why TraceLens's forensic engine is built the way it is. This is
mainly for me (and anyone reviewing the code), the code comments stay
short; the reasoning lives here.

## Why fuse signals into one score instead of showing separate messages

The original version had six independent checks that each printed their
own message. There was no way to know, overall, how suspicious an image
was, we had to read six lines and guess. Now every check returns a
`Signal` (score + weight), and `ForensicReport` combines them into one
0-100 authenticity score and a verdict.
Trade-off: a single number hides detail, so the individual signals are still shown underneath it, not
replaced by it.

## Why weights aren't all equal

Not every check is equally trustworthy. Finding "Stable Diffusion" text
directly in an image's metadata is close to direct evidence so that's
weighted at 2.0. A missing EXIF date is common even in real photos, so
it's weighted at 0.5. If everything counted the same, one weak signal
(e.g. missing date) could push an otherwise-clean image toward
"suspicious" and weighting stops that.

## Why thresholds/weights live in config.json instead of the code

Two reasons:
1. So they can be retuned after running `evaluate.py` against real
   images, without touching or risking breaking the actual logic.
2. It's honest about what's a guess. Several thresholds
   (noise_std_delta, edge_std) started as placeholder values, not calibrated
   against real data. Keeping them in config makes that visible and
   fixable, instead of buried as a magic number in the middle of a
   function.

**Update:** ran `evaluate.py` on a real labeled set, see the evaluation
section below for what happened. Several of these placeholder values
turned out to be wrong in ways that mattered.

## The evaluation story: what went wrong, in order

**Round 1:** first real test, 30 images. Result: 33% accuracy, almost
every authentic photo got flagged suspicious. Turned out my test photos
were all sent through WhatsApp, which strips metadata and recompresses
everything, and my noise/edge thresholds (15) were just guesses that
were way too strict for that kind of compression.

**Round 2:** raised the thresholds in config.json, re-ran, no change.
Took embarrassingly long to realize evaluate.py has its own --threshold
flag, separate from verdict_suspicious in config, I was only changing
what the app UI displays, not what evaluate.py actually tested against.

**Round 3:** fixed that, but now got 64% accuracy with 0% recall, it
flipped to calling everything authentic. Tried a few threshold values in
between (65, 48), none worked well. The two classes' scores were just
overlapping too much for any single cutoff to separate them.

**The actual fix:** added per-signal logging to evaluate.py to see what
each individual check was doing, not just the final score. Found that
clone_detection was maxed out (1.00) on almost every image regardless of
class, the scoring formula capped out past 10 duplicate blocks, and
basically any photo with a sky or blurred background hits that. It was
contributing weight for zero real information. noise_inconsistency and
edge_artifact_inconsistency were similarly overlapping between classes,
probably because both my "real" and "AI" images had been through some
recompression pipeline (WhatsApp/Snapchat vs. ChatGPT/Gemini/Bing's own
save process).

The one signal that actually separated the classes cleanly:
metadata_stripped_high_res. it fired on every AI-generated image
(all PNGs with zero EXIF) and none of the real ones.

Reweighted config.json: clone_detection down to 0, noise/edge down to
0.1, metadata_stripped_high_res up to 3.0. Re-ran:

**89.29% accuracy, 76.92% precision, 100% recall, F1 86.96%.**

The real lesson wasn't "find a better threshold", it was that two of
my six signals weren't discriminating between the classes at all, and no
threshold could fix that. Had to look at individual signals, not just
the combined score, to actually find the problem.

## Why the AI classifier API is a separate signal, not a replacement

The original AI-detection check only works if the image still has
metadata clues (a software tag, generation parameters in a comment
field). Anything with metadata stripped, which is common, since a lot of
AI tools and export processes strip EXIF, slips past it completely.

The Hugging Face classifier looks at actual pixel content instead, so it
catches a different set of cases. Rather than replacing the metadata
check with the API, I kept both as separate weighted signals. Evaluation
confirmed this was the right call in spirit but not in practice yet,
metadata-based AI detection turned out to have a real blind spot (see
evaluation section above), and the API signal that was meant to cover
that gap isn't currently reachable (Hugging Face restructured their
endpoint). Still the right design, just not fully working yet.

## Why the API call fails soft (returns None, never raises)

I don't want a slow/rate-limited/down external API to break the whole
report. If the HF call fails for any reason, no API key set, timeout,
rate limit, model still loading, the function logs a warning and returns
`None`. The rest of the analysis still runs and the report is still
useful, just without that one signal.

Trade-off: on a free HF tier, this signal will sometimes just be silently
missing from a report. Worth mentioning as a known limitation rather than
hiding it.

## Why the API key is an environment variable, not in config.json

`config.json` is meant to be readable/editable and safe to commit to
GitHub. An API key is a secret, if it were in config.json it could end
up committed by accident. `HF_API_TOKEN` as an environment variable keeps
it out of the repo entirely.

## Known limitations (things I know are weak, not hiding them)

- Clone detection (hash-based block matching) flags repetitive but
  legitimate textures, sky, brick, fabric, as false positives. A
  proper fix would be SIFT/ORB keypoint matching + RANSAC geometric
  verification instead of simple average-hashing. This is also why its
  weight is now set to 0 in config.json rather than relied on directly.
- noise_std_delta and edge_std were recalibrated from 15 to 50 after
  evaluation, but both signals still turned out to be weak
  discriminators overall (see evaluation story) and are now weighted
  near zero rather than relied on directly.
- Test set for evaluate.py is small (28 images), good enough to catch
  obvious problems (it did, three times), not a rigorous benchmark.
- Metadata-based AI detection only catches Stable Diffusion-style tools
  that embed generation parameters in EXIF. ChatGPT, Gemini, and Bing
  don't leave the same traces, so this check is blind to a large and
  growing category of AI-generated images. metadata_stripped_high_res
  ended up being the more reliable signal for these tools in practice.
- The AI classifier signal depends on a free-tier external API that is
  not currently reachable, Hugging Face moved this functionality to a
  newer "Inference Providers" system with a different API surface. The
  code and config are complete but the endpoint needs migrating.
- 3 images in the test set are still misclassified as suspicious and
  haven't been individually investigated yet.