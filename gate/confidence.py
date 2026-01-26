def check_confidence(plate_text: str):
    """
    Determines OCR confidence based on simple heuristics.
    Returns: (confidence_level, score)
    """

    if not plate_text:
        return "LOW", 0.0

    length_score = min(len(plate_text) / 10, 1.0)

    alnum_ratio = sum(c.isalnum() for c in plate_text) / len(plate_text)

    score = round((length_score * 0.6 + alnum_ratio * 0.4), 2)

    if score >= 0.75:
        return "HIGH", score
    elif score >= 0.5:
        return "MEDIUM", score
    else:
        return "LOW", score
