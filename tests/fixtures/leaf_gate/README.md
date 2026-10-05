# Leaf rejection samples

These small, fixed examples are used to test the local semantic gate. They are not a held-out accuracy benchmark. Exact image URLs and expected leaf/non-leaf labels are in `sources.json`.

- Eight healthy/diseased leaf images: [PlantVillage Dataset](https://github.com/spMohanty/PlantVillage-Dataset), Apple, Corn and Tomato folders. See the upstream dataset for citation and reuse terms.
- Bus and football players: [Ultralytics assets](https://github.com/ultralytics/ultralytics/tree/main/ultralytics/assets), provided under the upstream repository license.
- Dog: [PyTorch Hub example image](https://github.com/pytorch/hub/blob/master/images/dog.jpg).
- Parrots: [Hugging Face documentation image](https://huggingface.co/datasets/huggingface/documentation-images/blob/main/hub/parrots.png).

The integration test also generates five solid-color non-leaf inputs. Model thresholds should be assessed on the user's real leaf and non-leaf photos before drawing conclusions about general accuracy.
