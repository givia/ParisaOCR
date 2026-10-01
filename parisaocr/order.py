"""Reading order of detected lines on a page.

rtl: lines are grouped into columns by horizontal overlap (a line joins a
column when more than half of the narrower of the two overlaps), columns are
read right to left, lines inside a column top to bottom. raster: top to bottom
regardless of columns.
"""


def columns_rtl(lines, bbox=lambda l: l.bbox):
    columns = []  # [x0, x1, [lines]]
    for ln in sorted(lines, key=lambda l: -(bbox(l)[2] - bbox(l)[0])):
        x0, _, x1, _ = bbox(ln)
        for col in columns:
            if min(x1, col[1]) - max(x0, col[0]) > 0.5 * min(x1 - x0, col[1] - col[0]):
                col[0], col[1] = min(col[0], x0), max(col[1], x1)
                col[2].append(ln)
                break
        else:
            columns.append([x0, x1, [ln]])
    columns.sort(key=lambda c: -c[1])
    return [sorted(col[2], key=lambda l: bbox(l)[1]) for col in columns]


def order(lines, how="rtl", bbox=lambda l: l.bbox):
    """List of columns, each a list of lines in reading order."""
    if how == "raster" or not lines:
        return [sorted(lines, key=lambda l: bbox(l)[1])] if lines else []
    return columns_rtl(lines, bbox)
