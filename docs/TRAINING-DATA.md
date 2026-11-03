# What the classifier was trained on, and its overlap with our test set

DAT-848, 28 Oct 2026, a reviewer. L4 `PROTOCOL.md` part F requires "a statement of its training data, including whether Commons or DiffusionDB images are in it (known overlap). Without that line the run is not accepted." `python -m secondlook.calibrate freeze` now refuses to write a threshold file without it, and copies it into `threshold.json` as `training_data_statement`, with `training_data_overlap` and `training_data_sources`. The text lives in `TRAINING_DATA` in `secondlook/calibrate.py`. It was written for the pinned weights only (sha256 `b89f3627…`), so `freeze` refuses if those weights change and the statement is not rewritten.

## Verdict

**Same generator family (AI side).** The Stable Diffusion 1.x checkpoints themselves are among the detector's training generators, and our AI test images are Stable Diffusion 1.x output. So the AI side of the gate is an in-family test, not a held-out-generator test.

**No image-level overlap found (either side).** DiffusionDB's images are not in the training data. None of our 1,088 Commons files is in the released LAION training link list. Overlap with the *distribution* of our real photographs is possible and was not measured (see "Not checked").

## The detector

Community Forensics ViT-S/16 at 384 px, `OwensLab/commfor-model-384` revision `6076002bf0d9dd37537f965ee2f06f826c333b61`, weights sha256 `b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387` (pinned in `secondlook/rungs/classifier.py`). The model card says only "This model has been pushed to the Hub using the PytorchModelHubMixin integration" and lists `datasets: OwensLab/CommunityForensics, OwensLab/CommunityForensics-Small`. The dataset card links this checkpoint as the classifier it describes training ("for sampling the generators and training the [classifiers](https://huggingface.co/OwensLab/commfor-model-384)").

## Every training and validation source, from primary sources

Sources, all read on 28 Oct 2026 at the revisions given:

- **[P]** Park and Owens, "Community Forensics: Using Thousands of Generators to Train Fake Image Detectors", CVPR 2025, arXiv:2411.04125v2 (9 Jun 2025), PDF sha256 `4db0bab8…`. https://arxiv.org/abs/2411.04125v2. The arXiv PDF has no appendices, so Appendix C (hyperparameters) was not read.
- **[D]** Dataset card, `OwensLab/CommunityForensics` at `f0b06f79d20389706bb76c626391fa870e0bf604` (last modified 24 Oct 2025).
- **[M]** `data/metadata/systematic_set_metadata.csv`, `manual_set_metadata.csv`, `eval_set_metadata.csv` at the same revision.
- **[L]** `data/Real/laion_commfor_train_subset_2M.csv` at the same revision (322,608,120 bytes, sha256 `83c1e26e…c144820`).
- **[G]** Code README, github.com/JeongsooP/Community-Forensics at `ee5b71d43db0f3779e1edd64ee927b13f2dd6ad4`.
- **[DDB]** DiffusionDB dataset card, `poloclub/diffusiondb` at `fb620fbe49fa4420e0734bd9c0df11f51176b61f`.
- **[SD]** Stable Diffusion v1-4 model card, `CompVis/stable-diffusion-v1-4` at `133a221b8aa7292a167afc5127cb63fb5005638b`.

### Generated (AI) training images

| Subset | Size | What it is | Quote |
|---|---|---|---|
| Systematic | 1,919,493 images [D], 4,763 models [M] | Hugging Face `diffusers` latent diffusion models, taken "in the order of popularity, as indicated by the number of downloads" [P 3.1] | "We sample 4763 models with approximately 403 images each, for a total of 1.9M images" [P 3.1]. "a large fraction of these models adapt variations of a popular pretrained latent diffusion model, Stable Diffusion" [P 3.1]. "a vast majority of the generators we collect are derivatives of Stable Diffusion" [D] |
| Manual | 774,023 images [D], 19 models [M] | GANs (BigGAN, StyleGAN2/2-ADA/3/XL, ProGAN, ProjectedGAN, Gansformer, GigaGAN, StyleSAN, StyleSwin, CIPS), pixel diffusion (GLIDE, Guided Diffusion, DeepFloyd), latent diffusion (DiT, VQ Diffusion, Latent Flow Matching), Taming Transformers [M] | "we manually select 19 models from public repositories and sample an average of 40,738 images per model, resulting in 774K images total" [P 3.2] |

The code's training command uses exactly these two: `--huggingface_train_repo "OwensLab/CommunityForensics" --hf_split_train "Systematic+Manual"` [G]. The paper's training set: "We construct our training set of 5.4M images by pairing 2.7M generated images with 2.7M real images" [P 4.1].

**Stable Diffusion 1.x in the training generators.** [M] lists, in the Systematic subset: `CompVis/stable-diffusion-v1-1`, `CompVis/stable-diffusion-v1-2`, `CompVis/stable-diffusion-v1-3`, `CompVis/stable-diffusion-v1-4`, `runwayml/stable-diffusion-v1-5`, plus re-uploads and fine-tunes such as `botp/stable-diffusion-v1-5` and `cosmicBboy/stable-diffusion-v1-4-dreambooth-dog`. 4,463 of the 4,763 rows name `StableDiffusionPipeline` alone and 6 more name it with `DiffusionPipeline`. That pipeline serves both Stable Diffusion 1.x and 2.x, so the count is an upper bound on SD 1.x derivatives, not a count of them.

**How the AI training images were made.** "Text-conditional models are sampled using captions obtained from real images (Sec. 3.3). We either use captions that are already present in the dataset or use BLIP to generate them. Some models such as GigaGAN and HDiT do not provide a pretrained model, so we instead use their pre-generated images. Generated images are saved in PNG format" [P 3.5]. Every Systematic row has `RealSource` `coco,forchheim,imagenet,imd2020,laion,landscapesHQ,vision` [M]. So the authors generated the images themselves from real-dataset captions. DiffusionDB's images and prompts are not their source: "DiffusionDB" appears nowhere in [P], [D] or [G].

### Real training images

"we source real images from a variety of existing datasets: LAION, ImageNet, COCO, FFHQ, CelebA, MetFaces, AFHQ, Forchheim, IMD2020, Landscapes HQ, and VISION" [P 3.3]. The composition used for the released classifiers [D]:

```
LAION          40.30 %
imagenet       40.30 %
CelebA          7.15 %
COCO            4.39 %
LandscapesHQ    3.34 %
FFHQ            2.34 %
IMD2020         1.17 %
AFHQv2          0.59 %
VISION          0.25 %
Forchheim       0.13 %
Metfaces        0.05 %
```

"We clipped the LAION and ImageNet data to around 1.08M images to ensure that the ratio of real/fake is 1:1. We release the links to the LAION data subset we used for training here" [D]. Wikimedia Commons is not a named source in [P], [D] or [G].

### The backbone's pretraining

"We use a plain ViT-S backbone pretrained on CLIP objective using LAION-2B, ImageNet 21K, and ImageNet 1K datasets" [P 4.1]. LAION-2B is a web crawl and was not checked here.

### Validation and evaluation sets (not training)

"The evaluation set comprises 26K images sampled from 21 models not included in the training set" [P 3.4]: 11 commercial models (DALL·E 2 and 3, Ideogram V1 and V2, Midjourney V5 and V6, Firefly Image 2 and 3, FLUX.1-dev and schnell, Imagen 3) and Deci Diffusion V2, GALIP, Kandinsky 2.2, Kvikontent, LCM-LoRA-SDv1.5, LCM-LoRA-SDXL, LCM-LoRA-SSD1B, Stable Cascade, DF-GAN and HDiT, "sampled using RAISE, ImageNet, FFHQ, and COCO-based captions" [P 3.4, M]. The public version pairs these with COCO and FFHQ [D]. The paper also reports results on the Wang et al., Ojha et al., Synthbuster and GenImage benchmarks [P 4]. None of these is DiffusionDB or Commons. `LCM_lora_sdv15` is a Stable Diffusion 1.5 derivative, but it was held out from training.

## Our test set, for comparison

- AI: DiffusionDB, "14 million images generated by Stable Diffusion using prompts and hyperparameters specified by real users" [DDB], scraped from "channels where early users could use a bot to use Stable Diffusion before release" [DDB]. Our manifest records `generated_utc` 6 to 20 August 2022 and "exact version not recorded by DiffusionDB" (L4 `manifest-ai.csv`, `PROTOCOL.md` F).
- Real: Wikimedia Commons Quality Images and Featured Pictures, uploaded 2004 to 2021 (L4 `PROTOCOL.md` F).

## Overlap, at each level

| Level | AI side (DiffusionDB SD 1.x) | Real side (Commons) |
|---|---|---|
| Same images | **Not in it.** The authors sampled their own images [P 3.5]. No DiffusionDB link and no DiffusionDB image name is in [L]. | **None found** in [L]: it holds 2,034 Commons file URLs, and none is one of our 1,088 manifest files (primaries, reserves and unused, matched by Commons file name, so thumbnails count). Not checked: ImageNet and the other real sets, and copies of our photos at non-Commons URLs. |
| Same generator | **Yes, same family.** SD v1-1 to v1-4 and v1-5 are training generators [M]. Which SD 1.x checkpoint the Discord bot ran in August 2022 is not recorded, so "same checkpoint" can be neither shown nor ruled out. | n/a |
| Same distribution | Yes, by the above. | **Possible, not measured.** LAION is a web crawl, and so is the backbone's LAION-2B pretraining [P 4.1]. Stable Diffusion v1 "was trained on subsets of LAION-2B(en)" [SD]. LandscapesHQ supplies landscape photographs, as does one of our topics. |

## Reproduce

`scripts/training_overlap.py` holds the download commands at the pinned revision and prints the counts above. Output on 28 Oct 2026:

```
generators 4763; sd1_checkpoints CompVis/stable-diffusion-v1-1, -v1-2, -v1-3, -v1-4, runwayml/stable-diffusion-v1-5
laion_rows 2000000; laion_commons_urls 2034; manifest_real_files 1088
manifest_real_in_laion []; diffusiondb_links []; ai_names_in_laion []
```

A plain substring search for "poloclub" in [L] finds one row. Its host is `dbpoloclub.com`, an unrelated site (a 2017 WordPress upload), and the script does not count it.

## Decision

Overlap exists at the generator-family level. **Adopted (DAT-848 `08034af1`):** every gated result in `RESULTS.md` carries the label **"training-overlap: same generator family"**, next to the PASS, FAIL or INVALID and never in its place. The label line reads: "The detector was trained on images from Stable Diffusion 1.x checkpoints (v1-1 to v1-5); our AI test images are Stable Diffusion 1.x. This result says how it does on a generator family it was trained on, not on unseen generators." Reasons: `PROTOCOL.md` F already forbids an unseen-generator claim, and the label makes that visible where the number is shown. The S7 report already prints the full statement under the run table (`gate_report.py`). The alternative, relying on that printed statement alone with no separate label, was not adopted.

**Where the label is carried.** [RESULTS.md](RESULTS.md) prints it next to the v2 FAIL (run `gate-20261029-040012`, 29 Oct 2026, AEDT) and next to the v1 INVALID. The generated report [RESULTS-v2.md](RESULTS-v2.md) prints the threshold file's full statement under its run table, whose overlap verdict reads "same generator family on the AI side"; it does not print the label line itself.
