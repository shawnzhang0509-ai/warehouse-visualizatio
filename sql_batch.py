"""ODBC 批处理（DECLARE + SELECT）：遍历结果集，避免读到空的前置集。"""


def fetch_primary_result_set(cursor):
    """
    执行 cursor.execute 之后调用。
    返回 (columns, rows, has_result_set)。
    若多个 SELECT 结果集，优先返回第一个有行的；否则返回最后一个有列名的空集。
    """
    columns = []
    rows = []
    saw_columns = False
    while True:
        if cursor.description is not None and len(cursor.description) > 0:
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            saw_columns = True
            if rows:
                return columns, rows, True
        if not cursor.nextset():
            break
    if saw_columns:
        return columns, rows, True
    return [], [], False


def drain_cursor(cursor):
    """消费剩余结果集，避免下一次 execute 读到脏状态。"""
    try:
        while True:
            try:
                if cursor.description:
                    cursor.fetchall()
            except Exception:
                pass
            if not cursor.nextset():
                break
    except Exception:
        pass
