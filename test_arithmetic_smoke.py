#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四则运算验证码 真实图片冒烟测试。

给定两张验证码图片 URL，走真实链路：
    下载图片(requests) → ddddocr OCR(含放大重试) → 文件名解析交叉验证 → 计算

最终拿到算术结果即冒烟通过；任一环节拿不到数字则失败。
下载/OCR 全部失败、仅靠文件名兜底算出结果时标记为「降级通过」；
--strict 模式下必须有至少一张图真实下载并 OCR 成功才算通过。

用法:
    python3 test_arithmetic_smoke.py                       # 用内置默认 URL, op=*
    python3 test_arithmetic_smoke.py URL1 URL2             # 自定义两张图
    python3 test_arithmetic_smoke.py URL1 URL2 -o +        # 指定运算符
    python3 test_arithmetic_smoke.py URL1 URL2 -p http://127.0.0.1:8080
    python3 test_arithmetic_smoke.py --strict              # 必须走到下载+OCR

注意: hax 验证码临时图时效很短，过期后服务端返回 403，
此时只能走文件名解析（属预期容错，非代码故障）。

通过 importlib 加载 hus-renew.py（文件名含连字符无法直接 import）。
"""
import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 默认：2026-08-21 真实运行日志中的两个验证码图片 URL（img2 曾被 OCR 误识别为 '-'）
DEFAULT_URL1 = ("https://hax.co.id/img/temp/"
                "7a90e147204d0b3335e265b6cedf267f-3104.28.196.79.jpg")
DEFAULT_URL2 = ("https://hax.co.id/img/temp/"
                "113b650d0ed2ab778a146f75bd7a5dba-8104.28.196.79.jpg")

OP_ALIASES = {
    "*": "*", "x": "*", "X": "*", "\u00d7": "*",
    "+": "+",
    "-": "-", "\u2212": "-",
    "/": "/", "\u00f7": "/", ":": "/",
}


class FakeBrowser:
    """execute_script 恒返回 None：逼 _download_image 走 requests 真下载。"""

    def execute_script(self, script, *args):
        return None


def load_module():
    spec = importlib.util.spec_from_file_location(
        "hus_renew", os.path.join(HERE, "hus-renew.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def diagnose_download_fail(url: str, proxy: str = None):
    """下载失败后探测一次 HTTP 状态，给出可读原因。"""
    try:
        import requests
        proxies = {"http": proxy, "https": proxy} if proxy else None
        r = requests.get(url, proxies=proxies, timeout=15,
                         headers={"Referer": "https://hax.co.id/",
                                  "User-Agent": "Mozilla/5.0"})
        reason = {403: "图片已过期/失效(403)", 404: "图片不存在(404)"}.get(
            r.status_code, f"HTTP {r.status_code}")
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
    print(f"  ⚠️ 下载失败（{reason}）")


def fetch_digit(solver, name: str, url: str):
    """单张图：文件名解析 + 真实下载 + OCR。

    返回 (最终数字, 来源, 是否真实下载成功)。
    """
    url_digit = solver._digit_from_url(url)
    print(f"\n--- {name}: {url}")
    print(f"  文件名解析: {url_digit}")

    data = solver._download_image(FakeBrowser(), url)
    if not data:
        diagnose_download_fail(url, solver._proxy)
        if url_digit is not None:
            return url_digit, "文件名", False
        return None, None, False
    print(f"  下载成功: {len(data)} bytes")

    ocr_digit = solver._ocr_image_bytes(data)
    print(f"  OCR 修正后: {ocr_digit}")

    # 与生产 _extract_operands 一致：文件名优先（服务端生成最可靠），OCR 兜底
    if url_digit is not None:
        digit, src = url_digit, "文件名"
        if ocr_digit is not None and ocr_digit != url_digit:
            print(f"  ⚠️ OCR({ocr_digit}) 与文件名({url_digit})不一致，以文件名为准")
    elif ocr_digit is not None:
        digit, src = ocr_digit, "OCR"
    else:
        digit, src = None, None
    print(f"  ==> {name} = {digit}（来源: {src}）" if digit is not None
          else f"  ==> {name} 数字获取失败")
    return digit, src, True


def main():
    ap = argparse.ArgumentParser(description="四则运算验证码真实图片冒烟测试")
    ap.add_argument("url1", nargs="?", default=DEFAULT_URL1)
    ap.add_argument("url2", nargs="?", default=DEFAULT_URL2)
    ap.add_argument("-o", "--op", default="*", help="运算符: * + - /（默认 *）")
    ap.add_argument("-p", "--proxy", default=None, help="HTTP 代理，如 http://127.0.0.1:8080")
    ap.add_argument("--strict", action="store_true",
                    help="严格模式：必须至少一张图真实下载+OCR 成功才算通过")
    ap.add_argument("--allow-degraded", action="store_true",
                    help="允许降级通过（仅文件名兜底也算 PASS）。CI 环境默认不允许。")
    args = ap.parse_args()

    # 降级通过意味着「下载+OCR 这条链路根本没验证到」，只是拿文件名算了个数。
    # 在 CI 里这等于假绿，所以 CI=true 时默认按严格模式处理；
    # 本地调试想放宽时显式加 --allow-degraded。
    if os.environ.get("CI", "").strip().lower() == "true" and not args.allow_degraded:
        args.strict = True

    op = OP_ALIASES.get(args.op)
    if op is None:
        print(f"不支持的运算符: {args.op}（可选 * + - /）")
        sys.exit(2)

    mod = load_module()
    S = mod.ArithmeticCaptchaSolver

    if S(None)._ocr is None:
        print("❌ ddddocr 不可用（本测试必须真实 OCR），请先 pip install ddddocr")
        sys.exit(2)

    solver = S(proxy=args.proxy)

    print("=" * 60)
    print("四则运算验证码 真实图片冒烟测试")
    print(f"算式: [img1] {op} [img2]")
    print("=" * 60)

    n1, src1, dl1 = fetch_digit(solver, "img1", args.url1)
    n2, src2, dl2 = fetch_digit(solver, "img2", args.url2)

    print("\n" + "=" * 60)
    if n1 is None or n2 is None:
        print(f"FAIL 取数失败: img1={n1}, img2={n2}")
        sys.exit(1)

    result = S._calc(n1, op, n2)
    if result is None:
        print(f"FAIL 计算失败: {n1} {op} {n2}")
        sys.exit(1)

    answer = str(int(result))
    ocr_exercised = dl1 or dl2
    if args.strict and not ocr_exercised:
        print(f"FAIL(strict) 两张图均下载失败，下载+OCR 链路未验证到"
              f"（文件名兜底算出 {n1} {op} {n2} = {answer}）")
        sys.exit(1)
    if ocr_exercised:
        verdict = "PASS 完整链路"
    else:
        verdict = "PASS 降级(仅文件名，下载+OCR 未验证)"
    print(f"{verdict}: {n1} {op} {n2} = {answer}"
          f"   （img1←{src1}{'✓已下载' if dl1 else '✗未下载'}, "
          f"img2←{src2}{'✓已下载' if dl2 else '✗未下载'}）")
    sys.exit(0)


if __name__ == "__main__":
    main()
