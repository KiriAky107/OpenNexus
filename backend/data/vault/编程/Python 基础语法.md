***

title: Python 基础语法
tags: python, 编程
----------------
# 变量与类型

Python 是动态类型语言，变量无需声明类型。

整数、浮点数、字符串、布尔值是四种基本类型。

## 列表与字典

列表用方括号，字典用花括号。列表推导式非常常用。

### 函数定义

使用 def 关键字定义函数，支持默认参数与关键字参数。

```python
n = int(input())
total = 0
count_above_60 = 0
scores = []
min_score = float('inf')
max_score = -float('inf')

for i in range(n):
    while True:
        items = int(input(f"请输入第{i+1}个学生的成绩: "))
        if 0 <= items <= 100:
            break
        print("分数无效，请重新输入")
    scores.append(items)
    total += items
    if items > max_score:
        max_score = items
    if items < min_score:
        min_score = items
    if items > 60:
        count_above_60 += 1
print("=====成绩统计结果=====")
print(f"所有成绩: {scores}")
print(f"最高分: {max_score}")
print(f"最低分: {min_score}")
print(f"平均分: {total / n}")
print(f"60分以上学生人数: {count_above_60}")
print(f"60分以上学生占比: {count_above_60 / n * 100}%")
```

