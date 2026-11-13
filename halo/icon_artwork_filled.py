"""Filled glyph masks with size-aware optical compensation.
Generated directly at physical sizes; no runtime SVG dependency.
Large filled faces end at source centrelines; open strokes keep their original width.
Headphone cups retain their previous filled contour.
Source placement is retained and fine internal details are expanded.
The house roof has the requested pointed apex without scaling the glyph.
"""
import base64
from functools import lru_cache
import io
from PIL import Image

KINDS = frozenset(('headset', 'mouse', 'keyboard', 'microphone', 'gamepad', 'speaker', 'webcam', 'health', 'smart_home', 'watch', 'display'))
OPTICAL_PROFILE = {16: {'body_boundary': 'source-centreline', 'detail_growth_logical': 0.1, 'centre_growth_logical': 0.065, 'headset_cups': 'previous-filled-contour'}, 20: {'body_boundary': 'source-centreline', 'detail_growth_logical': 0.07565932872025408, 'centre_growth_logical': 0.04917856366816515, 'headset_cups': 'previous-filled-contour'}, 24: {'body_boundary': 'source-centreline', 'detail_growth_logical': 0.060240133573989656, 'centre_growth_logical': 0.03915608682309328, 'headset_cups': 'previous-filled-contour'}, 32: {'body_boundary': 'source-centreline', 'detail_growth_logical': 0.04204482076268573, 'centre_growth_logical': 0.027329133495745724, 'headset_cups': 'previous-filled-contour'}, 40: {'body_boundary': 'source-centreline', 'detail_growth_logical': 0.03181082915068203, 'centre_growth_logical': 0.02067703894794332, 'headset_cups': 'previous-filled-contour'}, 48: {'body_boundary': 'source-centreline', 'detail_growth_logical': 0.025327856188386417, 'centre_growth_logical': 0.01646310652245117, 'headset_cups': 'previous-filled-contour'}, 64: {'body_boundary': 'source-centreline', 'detail_growth_logical': 0.01767766952966369, 'centre_growth_logical': 0.0114904851942814, 'headset_cups': 'previous-filled-contour'}}
_PNG = {
    ('headset', 16): (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAAAAAA6mKC9AAAAcklEQVR4nGNgIBkwQihfJYb7m8AsJhDBdtKcmd3sGCtc2QkLEGl7'
        'FMb3bYHQ7d5QLUqfIAKfFKECkqZRIH6ssSRUS+v/TzkMDPmf/jdCVbxg4NVmYNDmZXgFFbj24/85BoZzDD+vwayxDwSRQXak+wsM'
        'AAEGGpfF3pnMAAAAAElFTkSuQmCC'
    ),
    ('headset', 20): (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAAAAACo4kLRAAAAk0lEQVR4nGNgoD5ghDE0rBmO3EQVdJ748hKLlnDhfiT1Ia9dQZT7'
        '20CEmOh7Pwgj+L0IXDDqOIx1PhxEMoEIw7MwwdP6cEExsUAOEM0RKCAN177l822w2tO3P2+Bq7zCoyJnxcBgqaDCcwUu+BeMGBj/'
        'MjD8g2t3fvv7ATcDA9eD3+9cEA51LpYFUbLFzuQFFhEAANs3JiLEywdcAAAAAElFTkSuQmCC'
    ),
    ('headset', 24): (
        'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAAAAADFHGIkAAAAtElEQVR4nGNgGEDACGdZ2hn+P3/wJLoC3oX/785acO//XB5Ucb47'
        'b71BtP/729woEvPfS0MYsu/mIIuL/feHMSP+CyFJxD1GsF+GgykmMOlyBiFxxhlJQpnZQRAiLOTwTxPJqJv///9LBDEy/v3/fw1J'
        'YvH////Bpl34////IiSjQKQ4iCGCIsTA8BeKwcQfJIkDP/7/2ANhMPzYhxSIPEGKL7Y8ZWBgkPERv7f2G3pA0h0AAAjrPpB7Opvw'
        'AAAAAElFTkSuQmCC'
    ),
    ('headset', 32): (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAAA+klEQVR4nGNgGBGAEZmj42Vu++vEye1XsKtl7fj3/9+lG3///21j'
        'xSavdvn/KUt2BgZ2y9P/L6lhyrPd+d/HAmGy9P2/xoahoO9/P4Iz8X83urzVvxtImjju/DNFU7D8fzoyN+f/QiiLCUqrMBxCVnCQ'
        'QQXNhI9vUflvXqKawMh3H1XBQzE0E/7////3uAmEbXr87////9AUvPn///+fJxBDX/75////azQF1/+DgASIKQ1mXkPzxX8wCQ4K'
        'FiQBhAKcAKbgK5j8CCI+MyBIhIK9DD///r0IVvDuxp9/Pxl2QyWgEcjQ+cGa89wCCDsk3vjboVm4bR2OAADtkWUB2F5SpQAAAABJ'
        'RU5ErkJggg=='
    ),
    ('headset', 40): (
        'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAAAAACpleexAAABNElEQVR4nGNgGAXUAIyYQmIyDI9fE9LHmr7l/f////+/25TCgk+d'
        '26P/cHDfCbe61L///1/qDhSTCey59P//3yRc6uL+//9dxgRhM5f//v8/Grs6s5//b+oguLo3//8wwqrwwv+fysh8zZ//T2JT5/v/'
        'fz6qSPH//x5YFK75fxQtUBlP/V+OqY7t2/8IdLHY/x+YYWyoJxkYVDkZ7qArvM3Ar4KhkI+B4RamQgYBDIXsDAyf0BW+ZWDghLGR'
        'Y/QAw9eTM15BOZLpptyYPmFgYNCAxPA9dgiXCxrrGhgKWaFpwQHC9YByWTHcCAtDBQgljSaMqZAN0+2oCgkBJpIV/vz2H0y/g3A/'
        'QEV/YWo5APblb0UIT/kvmLsbi9lSp//////ZF8YN+vr///8TEsS6bBRQGwAAoCeJrKXhADAAAAAASUVORK5CYII='
    ),
    ('headset', 48): (
        'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAAAAAByaaZbAAABaUlEQVR4nGNgGAUjAzBiE+Qyl5FgePH45HfizIjY9uM/GHzfEkKE'
        'ctMT/5HAMWNCTkqczczAcH3/jXussppO6gwMv1MW4TW/9v///7t1YDyDff///6/Bpz7q//9vycgCqd///4/Hrd729/+fJqhCZj//'
        '/7DApZ7pwf//UVgsvYk17CFyGzBFt/3/jyt0d///o4wpqvX//1bs6rn+/D+MTfzk/x/sSO5GMHWZGbAatYWBXQurBnkGhtvYNNwG'
        'S2HRIMvA8BKbhpdgKSwa+BgYsCbPzwwMQggeC4pc2nOGl6fOIgmYmIozSDHgABOgKXQGQmgOVKgPq4ZUWJp2gYl4wUSSsPrhCowB'
        '1+AIY1zHqgGeYhRhDHhoMuLXwIrBYMCqgTjARIGG30QwGJA1PIQx7uEWQQVnoKFuAxOwhwocZ8AO5A6CZD/GIEQSP4JE9skgKULN'
        'rpJKrC9v/0ESYFEV/333BQ4LRsGwBgDq7au3Bc7ANwAAAABJRU5ErkJggg=='
    ),
    ('headset', 64): (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAAB8ElEQVR4nO2WzytsYRzGv2ewkWLQoHSzuleJhGZ0b02SbLC4ulY2'
        'koV0u4ubv4DYWUspknulZiOyEStdEsJVcwfFRn5dP5rQmI55dGaaed8zznnfM1EszrOZ93ye7/uc73vO6X2HyJYtW+9DirlV0dRQ'
        'kFsUur46W5jbTT35+xE4HXanON3rR5J2vaksYehn9Ody/fw4w+VyO2Ow1+rtcxa1W970uePAMxDUyEIsSCrnnlbdn61jgxrzW0pI'
        '/wMg+GzF3jsAyw4LAaMAzsuf88oLAMPy+c0AQtVGTk0IQKNsvhIA0GrsfQOwIwvoAjBlZvoAtEsCtgC1xMz89AisiucXApgxt+eB'
        'SJ4eOfSX2up95gE+IuWrsIMxIJxlbjtVYETYQTFR4NY84PogWiIOOBV1eCoL+PDCACWT6F4UECTKFXaQuhT9VYToZI/o/nByJanw'
        'S3tJJtHHIuE2SqTGdzC1R2/8eIw7YWFD+2wT/MxzD+MBYcAkK9zg+TbjE1YfYhW3p+VXsLFi+S2UsWEphyOWA9KMqxyv/B043ltA'
        'mBs/SLFBwD9uzB3pfznsFzbkZh/MJs93GDc8M5h+JQo9HKW6BB4nsZxTsbqbDj3vjJ7PwO9s6f+D2hZP+uXS9P8k7Gqrz1NXZtck'
        'DdiyZevN9AS2TSgNN5MgvAAAAABJRU5ErkJggg=='
    ),
    ('mouse', 16): (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAAAAAA6mKC9AAAAWklEQVR4nGNgIBMorH/9eqsSgs/z5P/5i/+fCMEFIv7/T0v//z8B'
        'xGYCERpQjSCCBaooFKYaJuACEwBrQQZggQcQ9g24KP+H//////8gjlDnuP3Nmz2O5HoLAMh6Gvh9JuLsAAAAAElFTkSuQmCC'
    ),
    ('mouse', 20): (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAAAAACo4kLRAAAAgUlEQVR4nGNgoAgwwhjsmgzXf6LJ5X7///97Dqpgxv//x4///5+B'
        'LMbz5f9/ff3//7/wgLlMYFKHm4FBQpKBgVsHSdAAxAIxQQwGBhYw+YOBgaEOxoAJgoAFwgqIdjQAEXwB48IZDAwM4n//g8FfcWQN'
        'DRDBClRjckDezCUiQMgFAJpiLt/TtTaqAAAAAElFTkSuQmCC'
    ),
    ('mouse', 24): (
        'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAAAAADFHGIkAAAAoElEQVR4nGNgoD1gRDBlPP4ceICpQmP7/////29XQheXevv//+vX'
        '//+/kUaT2Pj//399/f///29EFef8+///fw3N/////+WCiDBBKH0ozcDApAehWSAUB4jgBjuRA0UCDBYhG44soYUsATcbHTDhkoAC'
        'LpBzweAvJ6rMApjEAjQtIp8g4q9F0A3TvgMSv6MN4yOCncdBg+HGgS8EHENVAAD89EZKYyqk7QAAAABJRU5ErkJggg=='
    ),
    ('mouse', 32): (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAAAk0lEQVR4nGNgGA6AEZkjUGhhwHDhRP8HHIo9n/4Hg6ee2OXd/8MB'
        'VhWCz/7////L0PgfyAwhLAoKQFpfMjC8AtEFcGEmOMsCWbUF8QoQ4D+yFf/hwixIShgYGESN/4ngCqj/2MURbsABqKngJbLwSywK'
        'LiAruEC8AgRQeI+IzfcKDFhAIEKBPwkJhhFZBaEkN3QBAMklYvgeyKW6AAAAAElFTkSuQmCC'
    ),
    ('mouse', 40): (
        'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAAAAACpleexAAAA4klEQVR4nGNgGAV4ACOmiJYlw7Hr//FpAgGJpR/+/////8MiSbzK'
        'mHLAysBKM5nwKOz4jwQ6cKuz+gtS8Dk+/jOI/muNSx33HbBJyxkYloMZd7iRXYXEdlUGU8wMDCxghrIrDoUa6FZo4FBoQKxCDWIV'
        'vkdX+B2JDXE2CvCMZ/DAFGXBFOJZgCnGgGI1XsA0OBTeRJe8iUPhBXSFF8hRiAyYzyGn2///zzMz4ACGP5HV/cRIJAjQgKywAbc6'
        'BubyXzBlvypwWgyx/S5E3V1DQgUAk7yaGsOtWw//4TVvFDCgAgCs3YhTlnx+wQAAAABJRU5ErkJggg=='
    ),
    ('mouse', 48): (
        'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAAAAAByaaZbAAABAElEQVR4nGNgGAVDEjBiERPw0tc3YLhw4cyOL0SZ4fn0PxTc9yRC'
        'ucCC/0hggQAh9UxHkdX//3+EiYCGCoi6f5s3/4OwCvCr1/sJUTafgWE+hPVFGc0JqNwkNgh9loHhHITFnYtXgwOmpQ74NAjrYmrQ'
        '5cSjwRRLmDCZ49FggameAc1NhIIZ00LswtoMDFrYZViwC0eBEQka+DJIdBJuwDTsNJzApuQEHu0S/7EACXwWvsBU/wKfkxguYJpx'
        'AZ8FDC4YFvx1xKuBYQK6hg786hnYTqCqP8VKQAODwFxk9fMIFmSgovIVTPlTT+IKY3ZDS0tLhuPHj5//SYT5o2BoAABtGMyI+hbd'
        'oQAAAABJRU5ErkJggg=='
    ),
    ('mouse', 64): (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAABTElEQVR4nO2WMUvDQBTH/wkBF8XJGpzEJS6luqij4iIWSqm1+AlK'
        'vlUGP4OugoPgUgRxiHukFR0cWuiQPMlyTczd8c6ICN5ve+T+v3s8QvIAi8WS42jGcHy6t7GOycv91c03huWFCQmSoWcYdy+eqUR8'
        'ruu1ymBWzhNNOzCgl37NE6UDfr5ZuT9ntsPNu08iFAVBJIpHlykIRSRrAH4mypCXX34Vibe8fhflZEXSLir011TqxhlLcKhu7oAl'
        '2FQLtusKfJbArytYUgtWWYLa0ILMB7YWLxKRqYCiILgs1tXTjkSgkTs/PwPXCvAnZzBXn/5gCcZqwfh3BA9qgeZRgRNSIvkmyhip'
        '8rwGgK5K0GUKMKrXANCS/1ybbAH6knza4+eBzrRyf9skD+zG5XzcMssD3rC4ZIXKJcvROI7a+/mal9xd35peb7H8Pz4B8vmdxxsF'
        'DvEAAAAASUVORK5CYII='
    ),
    ('keyboard', 16): (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAAAAAA6mKC9AAAAd0lEQVR4nGNgoBwwMjAwWDSxgdm/qs4wMDAxMDAUWH1X4uRW+m6V'
        'wsDAwMLAwPDk9rY/9xgU9sh9ZoCoYDCo4nJw4KrSArFBKhgYPrxiYnjzQQIhsOIAAwODQwNM4D9DkAMDA4MQWA4ksFafDWT7+91r'
        'qeAPkBEANY4Zm+9OFFoAAAAASUVORK5CYII='
    ),
    ('keyboard', 20): (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAAAAACo4kLRAAAAeUlEQVR4nGNgoBNgBBFKEWxQ7q/l92EyG/7DwQYQnwlE/P+reC96'
        '/tzoe8p//4P4LGClzI1SSQoMChI1zGAuWCUDQ1yD5PHj4k2JEB5UkOHXPyTbIdoZHgmsYWC4w/lIDlnwUD3YfeoQQTBYgnDSErjj'
        'lcOxOJ4eAABF4jDrtlsWDwAAAABJRU5ErkJggg=='
    ),
    ('keyboard', 24): (
        'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAAAAADFHGIkAAAAi0lEQVR4nGNgGIyAEUxq+nPARX5svI6Q3vAfCWwACzGBSWWGHRf/'
        'z3v9dt7/izsYlJAMvPzft+6L7J5dsl/rfP9fQpFAApeQjGJgYDie8n/arP/Jx2F8uATDfwYWEIEK8Bj1RogRDITeQAVYoLRQwzsw'
        'LSKEYtQKZKNWIAUJfxI/XNG7hR/xh+JAAgCIiVhBdUlMWQAAAABJRU5ErkJggg=='
    ),
    ('keyboard', 32): (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAAAsUlEQVR4nGNgGAUgwAgLBjUplAB5dgstfJb9RwPLUOUV/6Er+CcG'
        'kWGCUD8ZGW4Hn2VYEPf/d9wChrOBtxkYmVFMkPr/fwlD338vxlfPGL3+9zCs+v9fEl3Bvwt//n++8///nc///1z6h0UBOpBEcQMY'
        'zBG9yODoyHBRdA4DAiArwAekcFrBglD099RPKIvdjBmbCV0I7iSsJti5wU2wIsENBCOLEaZCQ4IBb3QzjHAAAL94o97ze1CHAAAA'
        'AElFTkSuQmCC'
    ),
    ('keyboard', 40): (
        'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAAAAACpleexAAAA/klEQVR4nGNgGAXDBnQ//Y8BnrZjqtPAVPb///9/6jB5JhgjAKs1'
        'jIEYCl8wMDC0hb1guB0yg4FhRuBthjdhbVBhVJDw//9HNobp/8sZxP//F2eo/z+dge3D//8JMHkWJLV89RdCGNJvujMwNOyJZgjY'
        'Z8CPzTUJWD3zPwHDjRDw48IfBoZHjxgY/lz4hSqDprDAsIbhuYLCM4YawzS8Cn8wMDBwMTAwMjAwcGBzINyNP8///v//4cP//3+f'
        '/47qRmRfMzAwsBkwMDDIgcRBDGSAqnDO8r9wNnNkCm6r5ZFF5PFYXYNsYiweq1Ow20ZMMlNhICrhPsSScEfBUAUAQ227tqLQs2EA'
        'AAAASUVORK5CYII='
    ),
    ('keyboard', 48): (
        'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAAAAAByaaZbAAABCElEQVR4nO2RsWrCUBSGfzUWNxWfQBCEjrWgi64ZOwp5gXToYzg4'
        '2BeQjNpVnbKIbrqIFpfuumVwrhn8JSBIknsSS7Fd8nG55FzOl3POvUBCwv+jdfdUsutklMIbRV6vWenr57NcvKEUHmQhqxQ87FLt'
        'CDbzA2BQbBHuU2ki/uaDZBuYcwPopA5saANtkkNR+KwYJ9IsT8lp2SRPRmUdKQgMxRniSYdOtu8rwLWsb2DV20a5l5bqqNIbdkRW'
        'Ub+hpQJy8FbB23M3VDj0HZK2TdLpH0IV/v6W3MeUj9oxRph9+eP1PEbQX/yxoQcStECcGovd/37ohZy1VJ5q3Z36FfadH/eRkHAH'
        'zuxp7IRakwBVAAAAAElFTkSuQmCC'
    ),
    ('keyboard', 64): (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAABgklEQVR4nO2VPUsDQRCG34vhEEQkaFIo5qsSkkIxioWNiXIiiI0/'
        'QbSztEtjc2n9B1ZiYZ9DLCzsLKwkTVQwKRRPooIiAR25u4gkWXfuRBvZh4Pdmd15mY+FAxQKheKX0Drs2EK6R3L97eLQluoVHonh'
        'bk4qcMPFE9Vl8Uk+nijZFhJqs4bgg0GJQIvGtbs0K5553nSXegMsOTfHfV0rElE1gmnHnEWkSkRFTd91jydZAQNIEJEJoEJ0BsAk'
        'ogRgCARCApmk+2Ec0EeAlO5uMeZ5OwkLfMWJlyUA+b3aVD8wYJ2O5gHsWL3LPnvAwJcQiNBfCZSG5503a2WzlvMECrGSL7ncZ51P'
        'ujc7w5udCegPgXrw/rV9ZXL9ZgpmNF8jonImUyaiWiHq5CPK4B+MMSz0Ph9cdflSq328XK5VpiE6XAnQg2PfTogFtjTBD2AzQA+2'
        '1y67fOl4AAHExbfZEmw/IfeyQ5t/iLdS9Q1eYL09QutQWJxhCjg5Yi4oFAoFfsgHfaWVO/UNQToAAAAASUVORK5CYII='
    ),
    ('microphone', 16): (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAAAAAA6mKC9AAAAYklEQVR4nGNgIBfInDghgyLQ8P9/A4TFhK4WLJAOYYMpsMAMiACY'
        'Agtc07zBwHBD8xqIzQgi6pi6ohiWlTBBzWVgkDxSp6lZd0QSYbRkw4sXDUh8BgaGephyFii9CWwYUQAActkVPLbXz58AAAAASUVO'
        'RK5CYII='
    ),
    ('microphone', 20): (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAAAAACo4kLRAAAAhElEQVR4nGNgoBIQE8MUS/39OxdD8MT//ydgbCYYQxyM0ASRAROY'
        'nCkN4UnPRJL678Bw////+wwO/5FUvhJkuMnAcJNB8CWYywImL6oy9MkwTGBQPY+k3f21DIiSe+WMbF/x++aIuMb3haiuUJ/1+css'
        'dQzHzZ8PZ0IsAoHbbNj8QQ4AADkjIcvDq7PSAAAAAElFTkSuQmCC'
    ),
    ('microphone', 24): (
        'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAAAAADFHGIkAAAAqElEQVR4nGNgoD1gRDAFvRm2vsem5sb//zewiWv8////vwacywRn'
        'ScAJNAk0wASh5FlhAqzyKBJX82ES+VdRJLh5YBI83CgSL8QZbv5nYPh/g0HyOYrEeWOG5+v+M6x7wWB+FsUNgf99GBikZBgYwv67'
        'o7pu2xuwgMfbdWjOltj9f+/y1Xv+7xLD8FHMul+/18Rg9ez+/UgcFiT2V6zKGRgYhIRwyVAGAL79LV4F8QjpAAAAAElFTkSuQmCC'
    ),
    ('microphone', 32): (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAAAw0lEQVR4nGNgGH5AYtGLF4sk8ChY/v/////L8Sj4AFLwAUWIEYX3'
        'H1OMiZCzmKinYP9+ZOGDcB7cQf9BTLgjwTwSrXjBIMHA8BHE+sjAIMnwAkPBBQZDBobtINZ2BgYjhnMYRrX/X4eIix3/mzEUKPz8'
        '7wxj+///Jo/pmvL/z10gLLeX/4uxOJf55P//S/zFZAOX/P9/ghmbh9g6/4Ii8///P+1sOPxsOfPQ//+HZpgz4AH/IYGJAARDkgWN'
        'f4CQhkEJANF8RyGoO+EJAAAAAElFTkSuQmCC'
    ),
    ('microphone', 40): (
        'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAAAAACpleexAAABHUlEQVR4nO2TsUoDQRRFz24RkjQhnQpWARVEjCksrPIRgj+wdgqJ'
        'xTapxa/IH4SQNiJsYbCw0cZCC2G7dCqSyG5zZdxCNs7uFqKN3uby3px5zMx7A//KkfM1tbfL9RWFKg0laVgqBDuGkzqLeXcx0UxZ'
        'DriRshzwLWU5YJbcnwS7g0Z6rTHoWvfM5QNB8o4B4GturVihmt5ZpfK9M05ZAl6SwNgyUyt4yw5wlwTGWtxYi58pdqEemruEdXBj'
        'nVrBA+kYqPVGo14NOJH2rWD5XtHWZ9iM9FC2X2w71qM55odaoeLNnKGNkmY4fiwdZXHgzaSny/HF5Fl69bI5WJ0kHZSClTwOWD88'
        'l8beWgFm1JbavzG4Fjn9fvH352/oHV6EbAesNt+SAAAAAElFTkSuQmCC'
    ),
    ('microphone', 48): (
        'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAAAAAByaaZbAAABW0lEQVR4nO2Uv0vDQBzFXwNqcbRWilIHERxUqoIVXAQJCC7StbOD'
        '4GCdBKcsivYPERzFpUO3ClKK0KqgIOhoh4Yi/gwWnlx+0EAvaapLkT4Ced/vvQ+XXO4C9PSPtFSo1wvLgePhowZJNg77AgLDusiT'
        'tSHJoCLpqXYwkgwIzLWYNsCXY8IBAV8pXQ5EN6OtiaisaavMsrhp1ncjNVdTOkMCCXcpbSroUMqfgHcgBqDq1MLEgDdPoGptnyun'
        'vgSwADx7AjfAPIDSmVWeViyg4vl82+TjAICxmlhUXezy/idyyxOYInlgOjWTUU1zTHLSE0Ce/J5xN6YbZM47jwmDvJtt1okH8nPc'
        'B0CapLFnr4Syb5BM+eWBXfG619mNkXgqeyv8jn8eWH+lSy9r7fJAPNfMn4+2DIckyMpqcjECvVTMXyCoNPswdNFP4N68ZArJ24Np'
        'nHx0OnlPv9cPgcuSqMR0oZkAAAAASUVORK5CYII='
    ),
    ('microphone', 64): (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAABo0lEQVR4nO1VTUsCURS9JhliSEKQkeQigjbRvo2LAnFjG3Hnwk39'
        'lRYVuKpVu6KNYYtId0Yfu9IWwkQGMSKo7fqgD4IT7730keM44xjhYg7CnDn3nuPM4857RDZs2DBGIl2tphOWVyqYA0cuaDHgXPiB'
        'M2v+ZNMPJC0FZGVA1lJATQbU9Lsc+iWYahuy9HB2wKAvYkxRYnrN3WotlICSYHIQgfZalxFDS9ROoqwN9CL2HfBKRB7O6lIUdPSn'
        '3D2gQUR+zopSFHSSbRKGAXUimudsR4qCLpgKKBLRMmeZXFM7yvDLEhEVyAgrABRB597FFD0HxP0dgIhhgOcLAPsvzcESBvDhMgyg'
        'XQD3I1rdXQGwbeynwCeAPa1+COBtwkQAbbD33mqbeUeKqetm/OS5Yb3HbGxa8PJT5sptKoD87G2hhqUSVpnyMN6h2dEpYfZkhl1O'
        '85dqxTk1vRgKsdtypGzuAYjIdwEN8j7TdiJyrj3+tjdWe/1ux1Iv0v606aXe4YoeFJj7ej86bOl01tsFB35L++OAW/7rA3FFifcV'
        'YMPGP+EbFTn5jD7TfUYAAAAASUVORK5CYII='
    ),
    ('gamepad', 16): (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAAAAAA6mKC9AAAAb0lEQVR4nGNgoBwwgggTd1YGBobfO85CBX3/Q4EHVGD9/7yS////'
        'n7+4moGBgYWBgUGxZxKDZP5yCQZZqIrX//Py//9o/Pn/PlTgN8yMLwwMDEwMDAx3Ge5rMDJq3md4AlVhkisMooTzjKngD5ARAM2p'
        'LUcjJtAkAAAAAElFTkSuQmCC'
    ),
    ('gamepad', 20): (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAAAAACo4kLRAAAAkklEQVR4nGNgoBNghJAG/GD644X/cBnD1/+h4LUeXHDN/59zfkJE'
        'V8EFH/5qZWj99f//f4N192BiLP+mMzAwzP7fuGXVk98QSxgY5P//P8Fw8v//xo3///8XgQqa/v//cz/UzP+aIK0QcTaNyV8Y+KLE'
        'GRg4oCqZ3/1/qwFiaL7//44ZZpN0jjKEoZolTe3QwQsAV6FHLNuKVOIAAAAASUVORK5CYII='
    ),
    ('gamepad', 24): (
        'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAAAAADFHGIkAAAAtUlEQVR4nGNgGIyAEUqrSkMZT28jS6td/w8HV5SRJHYjxP//34Zk'
        '3tf//8PCIMLX/3+BGc/AIPn/c7eISPfn////72fY/18ELmH5/xCIOvJ/Z9KX/V//G4I4LCBChsH2vy7DZQaGt09YHRgYpM7DJCQY'
        'GBiqQYzISBApBtfBz8DAEIFwiyiIYAIRtxgYGP7kcTAychT/YWBgeABXwvPw/yd3CNPj0//7PEg+1IFzeLRJDVV6AgCFX0zJsz5z'
        'AwAAAABJRU5ErkJggg=='
    ),
    ('gamepad', 32): (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAAA0ElEQVR4nGNgGAUkAcb0s7/+w8GPC4noCtr+o4EGVHnuf+gKfnNA'
        'ZJgglA0jA8NvI5P/CC0s1lAaQikyMDC8P8/wRhTM+23OdJpRDsUERYbfRuIMDGIQM1iY/v1hkEExQYGBBaz0H0T7GQYGNAUyDIxn'
        'XokzvBKFaGdlYGCQQlEgxcDAIGr8TwQUIiDtIC6KN7+j+/L//3sojuTADFthFAVvIMEpw8jIyCjbAeb8QlG+AmRoEYxXBeItR1Eg'
        'sfTVzzQEN/v3i4XimLaOAgbcAAC7S3C0j5vw/wAAAABJRU5ErkJggg=='
    ),
    ('gamepad', 40): (
        'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAAAAACpleexAAABJ0lEQVR4nGNgGAVDHDAimNIp6pJIXIb/z27Neo5Fi9+v/xjgpyem'
        'Ot7XmOr+/3/OC5NngjE8RLC5TMIZQ6ENiHipqPgSVaUNhkI1ELH8wYP1cDVrAtYwMKhjWHLj//+r7SEMDKHtV6HuU2dQ////PIbC'
        'L///20NY9v////97+e3/1f6r///HCB+B////N3CAWBwN////n84g8Q9s7D/kkAUBHbDwKgaG1WDGdAZxiML/UmiekUfVl3b5GtQo'
        'GagIC5QWB5PB8xmCIPp1YFrE0RRCgpspAcOTQmhW82CowKHwNZQ+YcnMyMjIyGx9CirwhwEVuEP8uBccQiDAuRci4o6mkGErSHQn'
        'O0KAfQ9IZCu6OgbG1MX7c5mRRViK9y9OQQ/vUTB0AQAg0J6aOm+NPgAAAABJRU5ErkJggg=='
    ),
    ('gamepad', 48): (
        'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAAAAAByaaZbAAABYklEQVR4nGNgGAWjYIAAIypX3VoOTcHDo7dw63Y79x8LOG+PS33S'
        'fxwgBrt6re+4NHxXR6hiQrKAA5fVHElYhS+CTbtlbHwLw4qz2NRzQuTSGRjykZT+27z53////1iwOEkJQrEyMPxDMmahr+9CBgZG'
        'WSwaFBgYGG6bME5hYJjMaHIbJvoDjBjQYwcE0iHugQCwq57PfP7/4/TpH////x+JRUPz////j0hA2BJHQM6XY5D/B/VKMRYnSTMw'
        'MFg//5/DwJD7/7k1SOQPw0+YpCRcGQuKBhTAuOaoNSypSWBx0lWI5UckwO5BA3uxaHjzHw+4hMUPAlgMwSLJBGcx49MgjEXDCxjj'
        'SxwvIxjwJ3yDiX3EYsg2qHNf6SLEdF9BBddj0WDwByz1UBFZUPUpWPC3FjZ3Rn76////GUQUgYHM+f///38Kx14ISDlJnDiGnFRB'
        'gNnG/On+Z9gsGAWjgM4AAAHjIh2YxwJzAAAAAElFTkSuQmCC'
    ),
    ('gamepad', 64): (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAABhUlEQVR4nGNgGAWjYBSMgsELOErX3fqPFdxaV0qEfrdH//GA+26E'
        '9Kf8JwCi8es3/EXIgB+6eA3YSUj///9bUHUwovB4PjAT8iPDL/4fyFwmFMkgmP4FGhoLcBjA5o/H9PlQZ/4TY2CQ+IfDD7MZcLtA'
        'Hkq/e8XA8OI9mukwV8nhccE9qCVvQJy3qBbDXXUNt36mPyCV89VhfPX5yAZIMDCIgQz4htsABZhNMIASDvPVoQaK4DTAHmYTDCj9'
        'Q9UKAQY4DYj+j+aFxVjjxBtZDwsyRxpMJiQwvAW58q0QPGwYGP4hKZPEGY0S2B3G2K6u3omUZJECCQ0sRQ5xqP+xgAmEXcDYrq7e'
        'iJpNGIhxweX/RIGdOF0gxkAUEKXUACGcBhAJhHEa8AKZs9OIEQY4jXcjy3zGafR2pJCaiCo1CUlqE04DghGKutHluhFyeIr2fTA1'
        'TZhycBM24NbPILEXoqYKm2QVRG43/riK2fjqxbZw7HLh2149WR+DV/soGAWjYBQMJAAAPyz0j0nWPI4AAAAASUVORK5CYII='
    ),
    ('speaker', 16): (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAAAAAA6mKC9AAAAdElEQVR4nGNgIBkwggi1CCYGBoZ/y+7ARA/8///x4///O0BskBTD'
        'YwaGFasYGF7BBRgYXrOxvIawIAK/NY4c0fwDZrKAyf16cxhuHkJSwfaNgeE7RA4iYM+gpsBui9Dyj/HkIzYJhn9wgfn3wZJ7SPcX'
        'GAAA+xIfITwOSxcAAAAASUVORK5CYII='
    ),
    ('speaker', 20): (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAAAAACo4kLRAAAAkUlEQVR4nGNgoD5gBJN67dxg+mvpNYTUhP///799+////w4wlwVM'
        '/mFgYMhiWcLAwIwkCALTEBphgn8W7WJwj4MoZGCCCtak/viRUo/ihp7/MuX//xcr/O9GUflRjYFB/T2amXpTeP5MNULTfkKGgUH+'
        'DFQ7ROU3BvOHN5jUGRm+I3lTYpIYmH5S8oK0UCIeAADjayxE+jxrcwAAAABJRU5ErkJggg=='
    ),
    ('speaker', 24): (
        'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAAAAADFHGIkAAAAnUlEQVR4nGNgGEDACKFk+kWhAq9zXyDLV/yHgwKICBOE+gciYpNB'
        'JCtEhAVJm9QvJA6SxK9opt9Q5QijGBgYVojp64qtwZQ4H6Uwa4pcxFUMo3b8n23KYGCzUxtdxycGVQYGLYZ3GEapMyxmYJjPoIEW'
        'AGX/v4gzuNsxSH7/X4qmg/tCzd1Xdec4MHTAAVQH1FXLTGGB+GI5mp6BAAAO1je6neKuYAAAAABJRU5ErkJggg=='
    ),
    ('speaker', 32): (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAAAwklEQVR4nGNgGGFg4uf/cPB5Iqa8wX8UYAATZ4IxJCDUunUQWhwm'
        'zoJqzq8Qlh9wPdgUsK1hQpVHV8AQhO42VPUnLAQErM4wYAUe////38gKYrFv+f//vzs2BU8EGAKWLw9gEHiGQ0EzQxQoCMIYOpAU'
        'ILvhEoMviPJjuIjDkWIMn0HUJwZRHAocGOZ+Y2D4MofBEZcvzBgkM5MlGCxx+eL/bRMQy+w+sgKUkFQ5ues6k4YrE+6gZvLwYCA7'
        'wTAMawAArsRmJrT/QaQAAAAASUVORK5CYII='
    ),
    ('speaker', 40): (
        'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAAAAACpleexAAABFElEQVR4nGNgGAXUAIzIHEE9ZO7/S+9xaOr48x8F/GnAro71Baq6'
        '//+fodgHB1Iw+fv3YSxJhCwTpoYHiooPMEWZMIV2gBE6YMEUSuNgiMPuC3Q3IoAkPhP/Xz3BYKnFSMjEf0tVQGzV5f/QTERX2Azj'
        'teJXeIOdwXTz69ebjBnY7+JVWMTA9hBk1EMWhnK8AX6OwUwORMuZMJxDlUFT+IZBBMIQY3iFV6Euwy0I4yaDHl6FBgzXVoHopTcZ'
        'jLF6BOaZt3IMjDlrV2QzMii+xR+O52UgHOnLBAL8/6dMIQYGkexP6HGNofD///83b2NJFEiA7Rl64nnBitXXv1YwoIE5vxFslORk'
        '6IeSXdddRtc5CigGAHuy03jl+C6sAAAAAElFTkSuQmCC'
    ),
    ('speaker', 48): (
        'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAAAAAByaaZbAAABNklEQVR4nGNgGAUjAzCi8FiSvfjRFHzcNvcPbu0T/2MB/Xjse4BN'
        'wwUUJUwoPG5spkjh0QAH70tK3jMQAV7DnFHPwFAPY78ixgYNMMIGWLALh/MweJPkJGTwihgn/bt0/R8JNhyxYWNgYLM5is0GLBr+'
        '5kN5jEV/idLQwsDAwGGgw8nAwNBCjIbzzAxsfX/+///dzcTAeokIDckMDBUQxxcxMKQSocGIgeE6RMNFBgZTIjSwMDD8hmj4ysDA'
        'TkQ8iDEwvIGw3jMwiGBIY2owYmA4B2GdBnPwApCTNjIw6H4FueiTOgPDJmLiwYWBQbJ1w5pWcQYGD6Ii7g4vjMt7lygN/x+5QHiO'
        'z7CkJUZUDdBQ2XviLJu+mTNUUIyQDWjgFZ5g/YrNlHd4NGzEpmE7NYvKUTB8AQA0jCBlPGi8PAAAAABJRU5ErkJggg=='
    ),
    ('speaker', 64): (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAABv0lEQVR4nGNgGAWjYBRQBTBiE9QSw6r21TXizPR9/R8HeOVHjH71'
        '/3iABoZyJgwRT3ymexBhgAI+AxSIMOA/PgP+EWEACligobGAgUTQjxRm/8QYGCT+IQn0keoCJmyuJsEAxkY+vmasaQ0OWPCbnxbG'
        'IMBAiQEMBLQTDAPCgAWf5I+zZ44zWJoas5MZjTN5ISL8s8mKxgeO6Z8hrI+pro9xKmPCJfHJ6gCCs8fiE8kG5DwHxUHi4sUJ/AwM'
        'DM/yGUgMg/UgZuIbEPN1HIi9GUcY4DLAjoGBwQkWdDYMDAzOJAbieQYGhoUwziKoADbAhF344WcGBlkZGE9RgoHh3TOSDLgKKpsR'
        'XB2oEPEGiDIwMLxEcF8xMDAIkmSAARMDwzV4SfD7CgMDkz5JBrBqMDD86obxOv4xMOiwMhAJ+sHxNQ1UnKyDxOI6UJEym8R08B+U'
        'EBhyL3x5dz6bAZEmSDDgPieyIOcDknOjwmqkYBdZI49LHRNOl3jfhFdyfje9yCmRRLfdOnDgAI+VlYMauUWamloaA80LVSYMkYf4'
        'lN8iwkix77jbFx9FiHFUwCtc+l9706eRNQpGwShgIAMAAMHiMVcidzeiAAAAAElFTkSuQmCC'
    ),
    ('webcam', 16): (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAAAAAA6mKC9AAAAf0lEQVR4nGNgIBkwgkmJXG2Gq5NfwEU1P/7/////R024wIb/Z9LT'
        'z/7fAGIzgQgDhgg1lVgGA7iKHz/YP37k+PkDLnDhf5aURN7/C3CBgv//n778/78ALsDS8/v//989LEjO4Zw+gxMqCSYt2dgZzX6e'
        'QLh0nZAIw5vXocR5DgDOMC0Urm2QxAAAAABJRU5ErkJggg=='
    ),
    ('webcam', 20): (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAAAAACo4kLRAAAAmElEQVR4nGNgoB3wPPv+/VlPFCHmtn///////6+ZGUnQ8f//l7Nn'
        'v/r/1wVJcMf/u/zycgL3/u9AEnzxv1L1/3+5uv8vwFwmqPBfEPEbxaId//czyMsyHELR7vL//87I6D3//zoiK50ActH//w2ojtea'
        '8+zZHC0MP82fD2fCbMcGpkDMnAPhMUIodj4GFsbfDJ9/4NGIDQAA02hCv4HKxVMAAAAASUVORK5CYII='
    ),
    ('webcam', 24): (
        'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAAAAADFHGIkAAAAwElEQVR4nGNgGBQgdu3Tp2tjMYTld/4Hg53yaBJH/v//e/ny3///'
        'D6OKJ/7/v1eCgUFq////iSgSO/7/V+Pr6uLV+f9/B4rEi//fGNP//09m/P3/BUSECSbF9J+RgYGZEc5ngVAX3NkdljL+WuXMxHAB'
        'xSjP///vqDAwaD/4/98N1VkzIN74/38Gmj9YK7+DhH9UsWL4Xbnx2bNmZQxhENi/H4kDdx46YITSli3MDPoMFxn+1hxH8QcYvMKl'
        'm0IAAHqjUITkOanrAAAAAElFTkSuQmCC'
    ),
    ('webcam', 32): (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAABHklEQVR4nN2SMUvDUBSFj1EUghZH28lFigErDuYHFF2cChYrCg5O'
        'Hf0Jjo6O/R9CHBR1FB3EpQouoijdWgoKKdRP0pKk5qUKDg6e6bx7Dufed9+T/gNGvpyy847q9cYwc86jBy+Xru80+zo0y2l6wQ91'
        '8BfNGcavC1L39E7OiiXdup1kQAl4ygds4RUoGR0OgXXZlYqtbeDAMFwCEzqGI2WAC8PQgrbkw7v0Bq2wboXkXprK6Fw606TdOyZQ'
        'AzZkb5ZtbQE1w1AFnp2AOS9A1djD9M2s9HFSH82vWtLjUjREBLcbb7KzbMiS9mPD3u9eU9JMsd0uZvUdIFGIFqUhGIvpmtufVdKV'
        'l+ZtxLcY+JUDCbtBwpwegoSfGv8tPgH+d55T0bM5GwAAAABJRU5ErkJggg=='
    ),
    ('webcam', 40): (
        'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAAAAACpleexAAABUElEQVR4nGNgGAXUAIzoAvwmRkb/z507+xG/Pr7Jf/+Dwd9JfPjU'
        'RTz/DwfPwnCry/iPAjJwqVP+gqrwizJ2dUxHwdKrDPn5DdeBmUeYsCq0AEvWQThNYI4FbhceYWAQSU4WZmA4htuVC0BSDgzy7/7/'
        'fyPL4AriLcCq8AZIiouhAkQVMwiAqBtIPkAwFRgYGH5+Y+AGsXkYPv6CCmEqvMDAwMCuwnAIxD7EoM4GFcKukCGCYXfzgQON+xki'
        '4ELYff3bGcJxBcd5AlaFQk9Bcv9mKDMwqM74B45uQawKGTyhUXf/MZThjl0dA8MM1LiegUsdA/cdZHV3wAGFA4S8gil75YlHGQMD'
        'A4fHhJv//1/tceVgIAgi/v8HByIqwJLiXoAROsCeNBkoUIgCbN4iB89bG5wmxggh84RikHkoJQV/PFilHsMlEPVuIYHSYugCAHoL'
        '9T4A7WOnAAAAAElFTkSuQmCC'
    ),
    ('webcam', 48): (
        'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAAAAAByaaZbAAABsklEQVR4nGNgGAVDEjBiF5WQYnj68j+xhrjPe///////7+a6E6U8'
        '6Px/ODgfSFA5c89/FNDJjF+98F5U9f//7+bDq2EKuvr//yfgU+8BV3b9OpzpgUfDJYiS1wl8DAx8Se8gvHPYgx4ELCAq9glAPXQA'
        'wrfAqaEDLP9egoGBUVudkYFB4iNYoAOnBoiJ2QwMtk/+/39ixcBQABY4gFPDA7C8FtQvpxkY9MACN3Bq+AGS/sXEwPoPxPjDyMD8'
        'C8T4gKyGCUUDiGDlY/j9GcT4+J+BhxXDUBQNL8CkAQPDEhC9iIHBFOJSBlxgBdjJGxgYWDLWLktjZmDYChZYgVNDAiTco2D8eAg/'
        'AqcGAXA++P+zDux0trqfYO5zfpwaGBqgyed2X2hU3x0opwC3egauU5ip9SxmSCEBBYijkMAHOXzqGRj80dT/9cavnoFB4SyKe+QJ'
        'qWdgYCn5AlP+pYyFsHqQ1+3L9v//f6zMgYuBaNDw/38Ddhkm4g2hlwZGNL59MbjgUpBneAhO1Z/69+M34AZ6TD8g4CSU7IjIVLid'
        'xKXPDqalGZ6C6Z8Xv6GpGAUMhAEATzp3vqPd510AAAAASUVORK5CYII='
    ),
    ('webcam', 64): (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAAB6klEQVR4nO1WO0jDUBQ9RltxsKWkioOjoPgHV3XVRSmixV3q6G/R'
        'wVkKLo7ubk6Ogoigtrr4Q9Sqi+CiWPyCqRS9EpOXNm1ibvxsOUtvzjvntO/dd2kADx48/AlK7JfKe4aawmFKp0+XV7Puk0PzL2Tg'
        'cT7k0u6fuicT7if9bvyBdSrCSoDvr08V+4nOGrj+8IWVn+i8iuf3Ja39RNs+VsCMnZ9ohuOvejb0m7FOWe4a3TaIp2pGwKJQv47p'
        't0yaVAS36OwPvulapTlHtgoyE3QMiIgvG89npwQbYe9gQ32IJh4eEv1qtcXew66uHAAwrJWDuZJ2HQOudGUjgGOtPADQptNXjgEZ'
        'Tfgu5eqMOtviFAv1kl1Qmfnx48NGJxUSNzrfBuBCq08AdEimZecAqAFzWql+tBcsO7dxT40eTD6mk2obSw/ZbYyIKzOdz84Kttcx'
        'IKgfPSktObJFXOU3v4thUibEME24GSbUGGrajHWH5O68cVY444wFskWc40fgyM6/U8EKQO21tf8yzPMDrU9W/rs6rh+QE8X+NZnv'
        'B6SRG7P9NmY7djaojOf/ucYr4R7lfUt7WaLs/lK/70fvByrIUSLhl5C8AFieQdR4Q/nqhI5UlH1eKethSv1LF/D9Fkw/gL8FDx7g'
        'Ep+vGIz9x/wNAAAAAABJRU5ErkJggg=='
    ),
    ('health', 16): (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAAAAAA6mKC9AAAAXklEQVR4nGNgoA5g6Xh7xc7p6tsOFqjAzP9QMBMq8ALEmTHt//8X'
        'UIHr//9v2qks8O3/ZagAPwMDgyiDAIQBBp3/v/Iq///fhLCn+/+E3v8NyDZ3/0flMzDER5PvDwAAnCvSqYxNYQAAAABJRU5ErkJg'
        'gg=='
    ),
    ('health', 20): (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAAAAACo4kLRAAAAkklEQVR4nGNgoCvIuPVli6jYji+3MhBiE/7/////6TMQOQEmxvwF'
        'xP3/5/b///+/sEAFjcFi/xt53/7//98AKij87////0WHDRka////IwDTzwEi9Bm4YEyIETPVGE6VMxz4ynCtEGG9wJn/f+W1/v8/'
        'A9cMAtyH/p/d9/8QN6rz2bf9/7+NHd1TLLNmwpxIEwAAYPVFk/viPXMAAAAASUVORK5CYII='
    ),
    ('health', 24): (
        'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAAAAADFHGIkAAAAsUlEQVR4nGNgGMTA+dT3x1O5eGY+/n7KGVm8+j8IXL0NpioR4tz/'
        '/iOB3zwgMSYQ4cIIUfDS/zEDAwOLA1xCGapz6aZuEKUCN8oFZMIJtfsuDPzf/v//D9YBAaxgMpKVH8GBmC6YwsXAsOEiw9znvxm+'
        'zP6E5F6Fe///b2RgePX//z15VB9K3/3/T8X+//+70uh+F7/x/87T/zfEMUNF5Mr//1dEsIWX4IHdgtjEBwAAAGtYUXUgtPb5AAAA'
        'AElFTkSuQmCC'
    ),
    ('health', 32): (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAAA3klEQVR4nGNgGAUowHHD3e9nu4UZGES7z36/u8ERPXhK/oPBSw/v'
        '1xBWCaq85X8MYIaiYCWmgpUoCh4gJDZDFd9HUfANLv+YgeEMmPEFIsMEoe5DqJdfGbYzMEwEs++hmNAG1vRZUv5rIAMD6wsQpxFF'
        'ASeM0cMLY3FgTyjsDLwd2GWKwS5kYmAIBNuWh6miAiQezsAwC0RXYDMj89///6cYGJ7+//8vE7st0f/+/6+f+v//v2js8gwMob9B'
        'xv8OxSXPwOD56///X5645RkYXH/9dMcnz8AgKYJffogCAPUTqE5k0YK8AAAAAElFTkSuQmCC'
    ),
    ('health', 40): (
        'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAAAAACpleexAAABNklEQVR4nGNgGAWDH3jPPv798mJHENNtyeWvJ2Z7Y1WmcOI/BNQy'
        'MjVDmSfkMdUJ34JK/v+/ey+ceVMYQ+Gi/1jBYnR10r+xK/wtA1XABKXNWZC1PWi5DWWxmKKZWIRijiODLoyZj2biL2Rd7w4yXD4K'
        'Zf9BU3gDpujV9PcMu/4xMEyF8q+hWc35CmpVEIP3/1gGBgbmF2DuMzZ0b/MjmFfEsApjAmMGhkwJdEFGOEs0mR1ML7rPwMCwU2kF'
        'yBff5r7DYpD6I7CrQL5g+wlmPlDGogwUOw9Asl95GRi8IeqksatjYJC+C5IvZWCYCqLv4lTHwCAOUvmU0/k9SJ04bnUMDOI3YFF3'
        'A686BgaRKxB1V0Twq2NgEASrvCJISB0Dg+Cx//+PY6pjxFTJFvl7FSzNjAJqAwAOpejsfHtwIAAAAABJRU5ErkJggg=='
    ),
    ('health', 48): (
        'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAAAAAByaaZbAAABXElEQVR4nGNgGAWjgAzAaSaGX4FU08Zr34612YI5bA3H//7/f6dH'
        'Crd5PT/+Q8BCbgYG8VNQzo8eTuzqWbZBVfz///+WvuFTBG8rC1YNC/7jBHOxqVf9h1vDP3XSLPj/fw4WDU/QFV07j2A/wKLhC5r6'
        'VQwMm+GcD1g0fEbT4M7AYAvnfMKi4QKq+q9sDAwMcEedgytjgrOuwlk/Eks+Mez/xcDAMAkmdAWLDRZwwysZGEr/Z4HE2F5Dhcyw'
        'aGBATjMCnxWQpaQZCIJsBgaGQgbSgMJPV0JKcqAuPgnmZcH8lIFbRy1UiSWIA4u2Gnx2lEDUrAaF0E8Iu4QYV/1VYGBwh6jPIeSP'
        'VHAqn8nAsA5E/0slpJ6BIQ6sY+4WsPo4wuoZGCL+wILnTwQx6hkYAn9D1P8OJE49A4P3L5D6X97EqmdgcP34//9HN6xSjNh1SAUw'
        'bHxKvAWjYBgCAKJBiBG8WyS0AAAAAElFTkSuQmCC'
    ),
    ('health', 64): (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAAB3UlEQVR4nO2UyytEYRjGH1Yug40x5TbKuOQfkLCiFBZ2Ui6LQcqG'
        'bJSFv0CysLCRhZRZWChRM1mQnURNKBK5X1LHbWHGvPpm5pzzHXO+mXOOstB5Fl/nfb739/Se0/k+wJYtW/9Eafq2p+DmXGOUFT6e'
        'GM3M9AaIiD4W62Sn3scMCnizjIzUe0Wy5rKZkzOvGNd9golVefaVbiIKOgHXEe8cVCCpPDd8N9FhUcmJ1rmtTMZX3lFKPVQlCdhO'
        'zRPtiPlyIzyRZoR0vhiGIXmFAY3GAhqEAU7dfr87Z1VjuITRn7qv7ARyJd54FU4Q1h3gEXhZEDLpfPGkF7DGlhninHthwJ4WDUbX'
        'DbacrXP+LkQa0Lz7JFxbRGexrRZuo1sY4Hjn2th9kCfRbHzvWNl4zhAGoPynMU3tCU1umFDpB7tDWmFdgwAynhy/SADQTj7xz5eg'
        '0cRzO8t92i6k1LjaXRtzLlVnKDUPTCjty9G6SuUnjPDAmNz/VczKEYUfM8YDwzIxxSq/XBm8b5j6IzFEygXyZb4fJtQTT1gCVmJP'
        'EVM8lISL0zjfY44HOsP8yYx0muWBjpDKhy3wQKtyRYY6rPBAczwhZPk0Nr1FA9qs8kC1X5I2a6zztmzZ+kN9A/ehFezbMmTNAAAA'
        'AElFTkSuQmCC'
    ),
    ('smart_home', 16): (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAAAAAA6mKC9AAAAV0lEQVR4nGNgIBuYmKDyy37+LEXm7/j3///fbXAu4+3/YHATJsD/'
        '///vJ0///P/PgxBoY2DohwgwQcSYGRh+Q1hQAQRgYWBg+PKHxbqBwZrhz3fy/YECAAl2I0Jd/QJlAAAAAElFTkSuQmCC'
    ),
    ('smart_home', 20): (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAAAAACo4kLRAAAAd0lEQVR4nGNgoCVoaMAQYjr1//9JJlQx6df//////1oKWczrO0js'
        '///vzggx1p8Qsf//f7CA+GBCgI2B4etnBl5uBnaeDzCVov////BkYHD58f+/ANhWqPj/3yAC5hRsTsYqCLbozQcBljgbBiUWhvcf'
        'ifQ/dQAAQzMuUvG9Re8AAAAASUVORK5CYII='
    ),
    ('smart_home', 24): (
        'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAAAAADFHGIkAAAAhUlEQVR4nGNgGCSguwOrMPvN//9vsGOKW3/7/////2/W6OI9f0Hi'
        '////bUEVP/IfDg4gi/MjxP//ZwELQUgBEPHuNwOrEEjVW4QO+f///78QZ2CQePP//39hsBATXPLnSwaGF9/gXIQEGiAg8RtZCMKB'
        'uOrZKzEGcZAHxBgYXn7CZcggAACUoUFj56jJTAAAAABJRU5ErkJggg=='
    ),
    ('smart_home', 32): (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAAAkUlEQVR4nGNgGImA/+E9fnzytj/+//9hjVu+4T8YNOCQZj4Jkf//'
        '/wQTNnmNTzD5//8/amDKp/xFyP///zcZJs4IM/8PmgaYBMw2LXQTxaA0C8wEBhD49Z6RgUGADSGAAAYgm/+AbU7+A2JLolkBBr/v'
        'g8j7vxmQAFYfD5CCR+gSH9AFTiGH9P//5wiZzDDCAACr2FUSFL7FZwAAAABJRU5ErkJggg=='
    ),
    ('smart_home', 40): (
        'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAAAAACpleexAAAAuklEQVR4nGNgGAW0Ajw8xKmz/fnTlhh1tf////9fS1AZ0zGQuv//'
        'jzLhV6f0AaLu//8PSvjUJfyBqfv//08EbnUr/6OAZTiUCT5GVff//312rApfoav7//8FVn9gqvv/XxAujQgFSWy6heAsFgy5d38Z'
        'GBiYESowgDXEskMCII7AHghPGS6NEQHHP4DID2fRxQnE1FBV+Aeb9F8sAX7yPyOIknUA82TB5P8H2HTPwYjpf7OIddgoGDgAAFm0'
        'm5V0IedgAAAAAElFTkSuQmCC'
    ),
    ('smart_home', 48): (
        'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAAAAAByaaZbAAAA2UlEQVR4nGNgGAUjG1ibk6a+5f//ThKUM5/+/////5PMxKrX+ARS'
        '////Bw3i1Cf8haj///9vNDHqV/9HAssIKhd8jKz+//+77PjV2/5EVf///3crfOpr0ZX/////XxVu9dOwqP////9EZDWMSGyW39jN'
        '+c/yD8FhQpKww2ExoyoDdg2suJzKgoMNBz8/g13CRyBIGdyhnvzhAnGJxw+ogDYOJ8HA2T1g6v+Oi1gkmbCIScEY4kRqwAuYRrqG'
        'r7gU/cSRNE5CaYEGGANK38dl0hQi8sMoGAXUAgC3zK467VOO/wAAAABJRU5ErkJggg=='
    ),
    ('smart_home', 64): (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAABLklEQVR4nGNgGAWjYBQMCSD4+KEgJfqdf/7//9ORfP0t/8GghUzt'
        'zGcg+v//P8NEjn6dzzD9//9/0iJdf+Y/hP7///+mkap/2380sJUk7VIv0fX///9Sgnj9vr8x9f///8uXWP2T/+MAk4nSznYNl/7/'
        '/6+yEdYv+xW3/v//v0oSNOApPv3//z9CV8+Ixhd4T8ACru+ofCY0eS9CLtRlwG8AEyEDWEnVQAiw4JH7/hUaQtwcDESDGESAf/Bl'
        'hogx+35AiFoT74XVm/9CGH83r8atigm31CsE8zVZBhAHmIhUN2rAyArEn4Q0/CRgwFFCBlwiUKQxvBKFsR7dhwsqysFYLwiWqjZI'
        'dSom+GTGQMgFDAxKapBS5utvmOR/Vm4w/efWPUL2j4JRMAqGLgAAljYuB/tiutEAAAAASUVORK5CYII='
    ),
    ('watch', 16): (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAAAAAA6mKC9AAAAWUlEQVR4nGNgIBeU/v2biyJw5f//8xAWE5iUFP79W1wSScHy/+cv'
        '/F+OJPDhf1rw/w8oAjsFVyMLLP///9VnZC2Sz3/9eiGNbO3F//9vIlvLsODfv3lk+woAJlgmrMKzePEAAAAASUVORK5CYII='
    ),
    ('watch', 20): (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAAAAACo4kLRAAAAgUlEQVR4nGNgoAgwwhiC+gwX36NJcr35//8ZG5qgw/////9bQjlM'
        'UNoaTiCA4FOQyqeCSEIMDK3//+cV/P/fCuGxQChtBoabEAoEYGYyMJjurIcxoYJXGRiaz1qBKJwWQVW+nwAiZ0O9BDPzKIg4gqKb'
        'gYHtxf//b7mhHLwBQiYAAGmtLNYmZOBSAAAAAElFTkSuQmCC'
    ),
    ('watch', 24): (
        'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAAAAADFHGIkAAAAmklEQVR4nGNgoD1ghLPYEqQYniz4g6kk5f////+j4FwmOMsDTqCB'
        'eyAdVzF1KAqCSDEZdPUMG/7/f/36//8V6OKm////NzH///+/KZpRxgwMDJ9f/oUwUOxgYGDQfWD5nwGbUf+Xlv//r4/FchDAsJxB'
        '7jdI/Lc8hgTDDZDES0wPMpwDEaewSGyHE2DAAmetYJdheLYQ0woaAgA+pkH8kdxKoQAAAABJRU5ErkJggg=='
    ),
    ('watch', 32): (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAAAw0lEQVR4nN1QMQrCQBAcbCyEtD5BsPAQbBULa7kv5B3iAwRfYmWZ'
        'XkHQCJZ2FjYxFiYIVpKRZJsTb7HWaeZ2Z9jZPeD/MM/JbKrrY1bou72aWwyERqrBCHXViJtEnLUJbQo3WsqAtQwgV349JPk0vYJk'
        '6DVEJC9ASjLyGhKSKXAlmfiXBBAEaC7eOo7hAKB+tMVGnp+wcsIuJy3ULSss/To65YXlrepfn8TwUJYE9kLbb4ZYS4CRiKFqwCQj'
        '7zNd/1m8ANqicr4zunXbAAAAAElFTkSuQmCC'
    ),
    ('watch', 40): (
        'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAAAAACpleexAAABG0lEQVR4nGNgGAV4ACO6AKuzOSPDv6MH/jIQAGv+g8FsQuo0IOr+'
        '/5NBk2BC49vBnGRLQKERjGFMQKExLoVogOkX1I3/v6LLoHJN38JYH3UZ8AC2izAD//+/gO4qZNAAUvElPuEbiK7ArY7pDUjBFgaG'
        'bSD6JYqRKBw9YRD5jYEB7BMxNZwKLcAkIwPD7l8ILhRgcTE/A8Ms4/cMDAwcDLiABdi7f1IYGBj2/v//H8VEFMD1FxIy+1TNv/z/'
        '/5cTp0KGCYhg/P9/Am51DGyXEepOsuFRyGAGj+tf5vjUMTB8hZvIjyqBHjynYYw7H/ErPAtjnGEgUyE6kPkHdaIJAwEwH6JuK7o4'
        'RgHA4mDFxPD/zK7fhEwcBQxIAAD9UKbUTBjavgAAAABJRU5ErkJggg=='
    ),
    ('watch', 48): (
        'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAAAAAByaaZbAAABKElEQVR4nGNgGAXDBkgtf/H/////TxYJEKlh4n8oqCVOPfd3mIY3'
        'TERpsIWp//9fF1OWCVPICME0prkGEwYiAOtfhB9+YZrHhCFi/R7BfmtN2ALuOwgL/v+/w0ZQwwSwwn+bN/8DMzoIqbeC+GAuA8NC'
        'MOOvNQE/5EEEzjEwnIbIpxLQoMFAQIAJja8AZ135TJSGHxBKkIHhgPoxJAGcYAckOB9LgvIFiLWDgA0XIJTM9UwGBmUkAZxA/Qcs'
        'zk51vvr///9PVQYCoAE5ov//ryCknoH9DLL6c8wENTA4QNIEJIV4E1bPwPQLyQYZIjQwnECof4nFPAYMcBbBPE2UhjNYmcTZgEUD'
        'FsD0BuaF38IMRIF8mIZu4tQzsJXdBCfAOh4iNYyCIQAA2KXkj+q+iPwAAAAASUVORK5CYII='
    ),
    ('watch', 64): (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAABlElEQVR4nGNgGAWjYBQQADorn/wHgwfzlMgJLOm3/+HgPi9OZUw4'
        'ZZKEEGyFEDIMcEPm+OBxKg7A9BXhg///HzOQDDSR9f//L4bTIgYcwAiVa0KpAcY0cwFOgBKG//8/YSARKKPqxx2KTNiFGVP/owr8'
        'z2UkyQEx6A74/z+RFP1OvzEN+OVKggEbYLoWqKsvgbE3EK9f/B9Uzz9xBgY5mAH/JIkOxEBYgL17ycDw6B2Uw+hPtAER2B0WwEAs'
        '+ABzNdjydzDeB6JdwIHCW4NdGB/4AbPzC5jr8ACPC7ACqIb///87gPmqED/dINoLF+CsjQkg8vY9MAerAVhBAVIC3K/CwKD6F8zM'
        'YCAWaCAn4Z+z+l5CWApEG8CwAzMrkJSUGTTg8YAAP2RIMIAhEdOAeFL0MzC0oeufQZp+BjF0AyxINIABWjPDwF9WUov1s6jca78p'
        'NOAcA4UGnGVgoCwUbUg2gOEpigFcpDcwTiNzrn0j3YAdODlEAoFHCA98USHDAAblhQ8h2p+vRavrR8EoGAUMGAAAJfS0lONe0xcA'
        'AAAASUVORK5CYII='
    ),
    ('display', 16): (
        'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAAAAAA6mKC9AAAAVUlEQVR4nGNgoBwwMjAwcGTyg9kfp/+ACGb8h4IEBgYGJgYGhqcw'
        '5W+gAigAq8BPGOcb1BaudKgtU39BBBgYzHQZGBgungExWcAC8pYMDAzvwQJkAAB5nBajTJKbWAAAAABJRU5ErkJggg=='
    ),
    ('display', 20): (
        'iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAAAAACo4kLRAAAASElEQVR4nGNgoBNgBBFcqYJQ7rs532AyCf/hIAHEZ8KmnYlWguff'
        'wbjvz+N3PAMDby4bAwPDr2kfwDwWiKCiG5jacQ6nbkoBAJsnFjFkGbHLAAAAAElFTkSuQmCC'
    ),
    ('display', 24): (
        'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAAAAADFHGIkAAAAUUlEQVR4nGNgGIyAEUxyJYrCRV7P/4aQTviPBCLAQkxgUgTZEGkk'
        'CSyAaSAlWMDktO8ID75YhEstBECChIGBIUIDTN1Yga7iEiQ8LuE3h04AANuzHLpAJeugAAAAAElFTkSuQmCC'
    ),
    ('display', 32): (
        'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAAAAABWESUoAAAAaElEQVR4nGNgGAUgwAgLBh4TlAA58wUtfGw+/0cBn23QFCxAlf//'
        'fwZUgglK/8QV4jAFDCNDwXV0iRtQmgVKzxeyQ5E/PJ+B6tHNwCAwTwjKepf0AZtaY0RUGmM1gUEPbsIlqrlwkAAAw2c35z0xIDwA'
        'AAAASUVORK5CYII='
    ),
    ('display', 40): (
        'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAAAAACpleexAAAAZElEQVR4nGNgGAXDBqjt+f0fDfzerYpF4YT/WMAEuDQTnKWPzRp9'
        'LAoJAKZRhQMVPAexSR/EIiY8/Tt6kvgyUZBh0IGwzwgHfg7Do3AHsld2oEgxovAko3ng7C9Ln1PdxaOAAQAXoFrBB5UiQwAAAABJ'
        'RU5ErkJggg=='
    ),
    ('display', 48): (
        'iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAAAAAByaaZbAAAAhklEQVR4nGNgGAWjYIAAIxKbJdONF4uSzztn/saueeJ/HKAfh20P'
        'cGm4gaSICYnNhsvdvDg0EAWYRjUQAZhGuoabuBTdwiFu+Rx76n5hgSPHMbDqYc1xl3BkOBoBRgwRPSEkzrtLBE2Yjerh2QQ1fEDV'
        '8AFdngVdICeGA4n3YwlBG0bBMAUAzI53Ij4PeJkAAAAASUVORK5CYII='
    ),
    ('display', 64): (
        'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAAAAACPAi4CAAAAqElEQVR4nGNgGAWjYBSMAioBRnQBZTs5DDE4+P/o0F0CBub8JwCy'
        'CLjgAz8BGz4KoPKZULkKhPQz8CvgNUCEkH4GBmG8BpAOmMjQM2oAGmBioBAwjRrAMBoGDIMgDBhRudxfSNXBhMr9WkNIfxl+8xgY'
        'Agzw6r+wgZANIxAwYhc2c2VFE/m9+xQJxnpgq1Y9SEiJAUQLMmA3AGtkb6BnGIyCUTAKGEgFAOq6SNGA3ihTAAAAAElFTkSuQmCC'
    ),
}

@lru_cache(maxsize=128)
def image(name, size):
    choices = [n for kind, n in _PNG if kind == name]
    source_size = min(choices, key=lambda n: (abs(n-size), -n))
    with Image.open(io.BytesIO(base64.b64decode(_PNG[name, source_size]))) as source:
        result = source.copy()
    if source_size != size:
        result = result.resize((size, size), Image.Resampling.LANCZOS)
    return result
