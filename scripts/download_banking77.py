from datasets import load_dataset

dataset = load_dataset("PolyAI/banking77")

print(dataset)
print(dataset["train"][0])
print(dataset["test"][0])
