class TextSegmenter:
    """
    UI-aware OCR text segmentation.

    The OCR engine can sometimes combine adjacent UI labels
    such as:

        GoRunTerminal

    This class uses a known vocabulary to recover the
    individual labels and estimate their positions.
    """

    def __init__(self, minimum_confidence=0.85):

        self.minimum_confidence = minimum_confidence

        self.ui_vocabulary = [
            "File",
            "Edit",
            "Selection",
            "View",
            "Go",
            "Run",
            "Terminal",
            "Help",
            "Explorer",
            "Search",
            "Source Control",
            "Run and Debug",
            "Extensions",
            "Settings",
            "Save",
            "Open",
            "New",
            "Cancel",
            "OK",
            "Close"
        ]

    def _center(self, box):

        x_values = [
            point[0]
            for point in box
        ]

        y_values = [
            point[1]
            for point in box
        ]

        return (
            int(
                (
                    min(x_values)
                    + max(x_values)
                ) / 2
            ),
            int(
                (
                    min(y_values)
                    + max(y_values)
                ) / 2
            )
        )

    def _find_vocabulary_matches(self, text):

        normalized = text.lower()

        matches = []

        for item in self.ui_vocabulary:

            item_normalized = item.lower()

            start = 0

            while True:

                position = normalized.find(
                    item_normalized,
                    start
                )

                if position == -1:
                    break

                matches.append({
                    "text": item,
                    "start": position,
                    "end": (
                        position
                        + len(item_normalized)
                    )
                })

                start = (
                    position
                    + len(item_normalized)
                )

        # Remove overlapping matches.
        matches.sort(
            key=lambda item: (
                item["start"],
                -(item["end"] - item["start"])
            )
        )

        selected = []

        occupied_until = -1

        for match in matches:

            if match["start"] >= occupied_until:

                selected.append(match)

                occupied_until = match["end"]

        return selected

    def segment(self, ocr_item):

        text = ocr_item["text"].strip()
        confidence = ocr_item["confidence"]
        box = ocr_item["box"]

        if not text:
            return []

        if confidence < self.minimum_confidence:
            return []

        matches = self._find_vocabulary_matches(
            text
        )

        if not matches:

            return [{
                "text": text,
                "confidence": confidence,
                "box": box,
                "center": self._center(box)
            }]

        x_values = [
            point[0]
            for point in box
        ]

        y_values = [
            point[1]
            for point in box
        ]

        left = min(x_values)
        right = max(x_values)
        top = min(y_values)
        bottom = max(y_values)

        total_characters = len(
            text
        )

        if total_characters == 0:
            return []

        total_width = right - left

        segments = []

        for match in matches:

            start_ratio = (
                match["start"]
                / total_characters
            )

            end_ratio = (
                match["end"]
                / total_characters
            )

            segment_left = (
                left
                + total_width * start_ratio
            )

            segment_right = (
                left
                + total_width * end_ratio
            )

            segment_box = [
                [
                    segment_left,
                    top
                ],
                [
                    segment_right,
                    top
                ],
                [
                    segment_right,
                    bottom
                ],
                [
                    segment_left,
                    bottom
                ]
            ]

            segments.append({
                "text": match["text"],
                "confidence": confidence,
                "box": segment_box,
                "center": self._center(
                    segment_box
                )
            })

        return segments

    def segment_results(self, ocr_results):

        segments = []

        for item in ocr_results:

            segments.extend(
                self.segment(item)
            )

        return segments

    def find(self, ocr_results, target):

        target = target.lower().strip()

        if not target:
            return None

        segments = self.segment_results(
            ocr_results
        )

        exact = []

        partial = []

        for item in segments:

            value = item["text"].lower()

            if value == target:

                exact.append(item)

            elif target in value:

                partial.append(item)

        if exact:

            return max(
                exact,
                key=lambda item: item["confidence"]
            )

        if partial:

            return max(
                partial,
                key=lambda item: item["confidence"]
            )

        return None


if __name__ == "__main__":

    print("========================================")
    print("        NR AI TEXT SEGMENTER")
    print("========================================")

    segmenter = TextSegmenter()

    test_item = {
        "text": "GoRunTerminal",
        "confidence": 1.00,
        "box": [
            [279.0, 14.0],
            [439.0, 14.0],
            [439.0, 32.0],
            [279.0, 32.0]
        ]
    }

    print("\n🔎 OCR input:")
    print(f"  {test_item['text']}")

    segments = segmenter.segment(
        test_item
    )

    print("\n🧩 Segments:")

    for segment in segments:

        print(
            f"  • {segment['text']}"
            f" | center={segment['center']}"
        )

    result = segmenter.find(
        [test_item],
        "Run"
    )

    if result:

        print("\n🎯 Exact target:")

        print(
            f"  Text: {result['text']}"
        )

        print(
            f"  Center: {result['center']}"
        )

    else:

        print(
            "\n❌ Target not found."
        )

    print(
        "\n🟢 Text segmentation test completed."
    )