import rasterio


def some_test():
    """Some test"""
    return rasterio.Affine(a=0, b=0, c=0, d=0, e=1, f=1)
