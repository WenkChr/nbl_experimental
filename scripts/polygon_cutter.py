import geopandas as gpd
import numpy as np
import os
import swifter
import pandas as pd
import sys
import shapely
from pathlib import Path
from shapely.geometry import MultiLineString, Polygon, MultiPolygon, LineString
from shapely.validation import make_valid
sys.path.insert(1, os.path.join(sys.path[0], ".."))

'''

Inital flow:

- get all lines that a polygon intersects with
- then for each line in the linkage split the polygon along the lines
- create methods for exporting splits, slivers, other geometry subsets

'''


class PolygonCutter:
    
    '''
    Splits an input polygon where it intersects the input cut geometry.
    Cut geometry must be polygon or lines no points accepted.
    Output geometry is then cleaned to keep only valid splits
    '''

    def __init__(self, bld_poly: gpd.GeoDataFrame, cut_geom: gpd.GeoDataFrame, crs=4326, proj_crs=32614) -> None:
        
        def reproject(ingdf: gpd.GeoDataFrame, output_crs: int) -> gpd.GeoDataFrame:
            ''' Takes a gdf and tests to see if it is in the projects crs if it is not the funtions will reproject '''
            if ingdf.crs == None:
                ingdf.set_crs(epsg=output_crs, inplace=True)    
            elif ingdf.crs != f'epsg:{output_crs}':
                ingdf.to_crs(epsg=output_crs, inplace=True)
            return ingdf

        
        def ValidateGeometry(input_geometry) -> gpd.GeoSeries:
            '''Checks if input geometry is valid and if invalid attempts to make it valid accepts Geodataframes and Geoseries'''
            if type(input_geometry) == gpd.GeoSeries:
                input_geometry = input_geometry.apply(lambda geom: make_valid(geom))
            if type(input_geometry) == gpd.GeoDataFrame:
                input_geometry = input_geometry['geometry'].apply(lambda geom: make_valid(geom) if not geom.is_valid else geom)
            return input_geometry


        def check_geom(input_gdf: gpd.GeoDataFrame, geometry_column= 'geometry') -> gpd.GeoDataFrame:
            '''Checks to see if the input  geometry is a line. If polygon converts to lines. If points or other returns a geometry error'''
            
            def ToSingleLines(geom: Polygon) -> MultiLineString:
                '''Converts polygons into single lines'''
                
                # temp measure to remove GeometryCollections and None cases
                if geom.geom_type not in ['MultiPolygon', 'Polygon']:
                    return None
                bounds = geom.boundary # returns the boundary as a linestring of many lines. Need to cut this into individual lines
                # multilinestrings need to be handled slightly differently
                if bounds.geom_type == 'MultiLineString':
                    # inefficient way to do this but need to extract each line from a multilinestring perhaps look at this again later 
                    bounds = [l for l in bounds.geoms]
                    bounds = [list(map(LineString, zip(l.coords[:-1], l.coords[1:]))) for l in bounds]
                    bounds = [ls for l in bounds for ls in l]
                    return bounds
                # if its just a single line string then its simple to deconstruct
                return list(map(LineString, zip(bounds.coords[:-1], bounds.coords[1:])))
                                                        

            input_gdf.reset_index(inplace=True)
            if input_gdf.geometry[0].geom_type in ['LineString', 'MultiLineString']:
                # If the geometry is already in line type then just strip attributes 
                return input_gdf
            
            # If inputs are polygons then convert them to lines and strip attributes
            if input_gdf.geometry[0].geom_type in ['Polygon', 'MultiPolygon']:
                input_gdf = input_gdf.explode(index_parts=False)
                input_gdf['geometry'] = input_gdf['geometry'].apply(lambda p: ToSingleLines(p))
                
                return input_gdf.explode(index_parts=True)

            # If the geometry is a point or mutipoint raise an error
            if input_gdf.geometry[0].geom_type in ['Point', 'MultiPoint']:
                raise IOError('Shape is not a Polygon or Line')


        def FindIntersects(input_geom: gpd.GeoDataFrame, search_geometry: gpd.GeoDataFrame, input_link_field: str, search_link_field: str) -> gpd.GeoDataFrame:
            '''finds all intersections between the input geometry and the search geometry'''

            joined_geom = gpd.sjoin(input_geom, search_geometry[[search_link_field, 'geometry']], op='intersects')
            input_geom['line_ints'] = input_geom[input_link_field].swifter.apply(lambda x: tuple(joined_geom[joined_geom[input_link_field] == x][search_link_field].tolist()))
            return input_geom


        def DropDuplicates(gdf: gpd.GeoDataFrame, dfield: str) -> gpd.GeoDataFrame:
            ''' 
            Alternative to pandas.drop_duplicates for dropping duplicate geometry records in a geodataframe
            Inputs must be of type point multi geometry not accepted
            '''

            # Extract x and y values from the points
            gdf['x'] = gdf[dfield].x
            gdf['y'] = gdf[dfield].y
            gdf['xy'] = gdf[['x','y']].to_numpy().tolist()
            # Group the points by x and y then cound the number of each instance
            # xygroup = gdf.groupby(['x', 'y']).size() # size returns row count for a group count for single
            # print(gdf[gdf.xy.isin(xygroup[xygroup > 1].tolist())])
            print(gdf.columns())
            sys.exit()
            gdf = gpd.sjoin(gdf, gdf, how='left')
            print(gdf.head())

            sys.exit()
            multigdf = gdf[gdf.index.isin(xygroup[xygroup > 1].index.tolist())]
            print(len(multigdf))
            # multigdf.drop_duplicates(subset=dfield, inplace=True)
            print(len(multigdf))
            
            sys.exit()


        def CutPolygon(input_geom, line_geom) -> MultiPolygon:
            '''Cuts the input polygon by the lines linked to it during the FindIntersects Step
            Run the FindIntersects step before calling this function'''
            
            # Select only key vars and set the cut indexes
            line_geom = line_geom[['cut_index', 'geometry']]
            input_geom = input_geom[['geometry', 'line_ints']]
            cut_indexes = input_geom['line_ints']
            
            if len(cut_indexes) == 0:
                return input_geom['geometry']
            if len(cut_indexes) >= 1:
                # retrieve the records related to the cut indexes
                cutters = line_geom[line_geom['cut_index'].isin(cut_indexes)]
                # For every cut index split the polygon by it. Returns as a list of geometry collections
                geoms = [shapely.ops.split(input_geom['geometry'], c) for c in cutters['geometry'].values.tolist()]
                # Extract all geometry from the geometry collections

                geoms = [p for gc in geoms for p in gc.geoms]
                # Take that list and convert it to a multipolygon. Return that 
                if len(geoms) < 1:
                    print(geoms)
                    print(MultiPolygon(geoms))
                    sys.exit()
                return MultiPolygon(geoms)


        # Load in the inputs to geodataframes
        self.bp = bld_poly
        cut_geom = cut_geom
        # Ensure projection consistency
        self.bp.to_crs(crs=crs, inplace=True)
        cut_geom.to_crs(crs=crs, inplace=True)

        # Ensure all valid geometry
        self.bp['geometry'] = ValidateGeometry(self.bp)
        cut_geom['geometry'] = ValidateGeometry(cut_geom)
        
        # Calc unique values for data to link between datasets
        self.bp['bp_index'] = range(1, len(self.bp.index) + 1)
        cut_geom['cut_index'] = range(1, len(cut_geom.index) + 1)

        # Drop Non-Essential Cut Geometry
        cut_joined = gpd.sjoin(cut_geom, self.bp[['bp_index', 'geometry']])
        cut_joined = list(set(cut_joined[~cut_joined['bp_index'].isna()]['cut_index'].tolist()))
        cut_geom = cut_geom[cut_geom['cut_index'].isin(cut_joined)]
               
        # convert the cut geometry to lines if necessary
        print('Converting line geometry')
        self.line_geom = check_geom(cut_geom)
        self.line_geom = self.line_geom[self.line_geom['geometry'] != None]
        self.line_geom.reset_index(inplace=True)
        if type(self.line_geom) != gpd.GeoDataFrame:
            self.line_geom = gpd.GeoDataFrame(self.line_geom, geometry='geometry')
            self.line_geom.set_crs(crs=crs, inplace=True)
        self.line_geom['line_index'] = range(1, len(self.line_geom.index) + 1)
        
        # Drop lines that do not intersect a building
        lines_joined = gpd.sjoin(self.line_geom, self.bp[['bp_index', 'geometry']])
        self.line_geom = self.line_geom[self.line_geom['line_index'].isin(list(set(lines_joined[~lines_joined['bp_index'].isna()]['line_index'].tolist())))]
        
        # Project data for overlap checks
        self.line_geom = reproject(self.line_geom, proj_crs)
        self.bp = reproject(self.bp, proj_crs)

        # Delete lines that overlap
        self.line_geom['centroid'] = self.line_geom.geometry.centroid
        self.line_geom = DropDuplicates(self.line_geom, 'centroid')
        print(len(self.line_geom))
        self.line_geom.drop_duplicates('centroid',  inplace=True) # Find a different method this is very very slow.
        print(len(self.line_geom))

        sys.exit()
        # For testing purposes export lines here to be deleted later
        #self.line_geom.to_file(Path(os.getenv('OUT_GPKG')), layer='parcel_lines')
        print('Finding intersects')
        bp = FindIntersects(self.bp, self.line_geom, 'bp_index', 'cut_index')
        print('Cutting by intersects')
        cut_geom = bp.swifter.apply(lambda x: CutPolygon(x, self.line_geom), axis=1)
        self.bp['geometry'] = cut_geom
        self.bp = self.bp.explode(index_parts=True)
        self.bp.drop(columns=['line_ints'], inplace=True)
        # Clean up results and remove slivers
        self.bp['split_area'] = self.bp.geometry.area

        
    def __call__(self, *args, **kwds):
        pass

def main():
    # setup for testing purposes
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), 'cutting.env'))

    aoi_path = Path(os.getenv('AOI_EXTENT'))
    aoi_lyr_nme = os.getenv('AOI_LYR_NME')

    parcel_path = Path(os.getenv('PARCEL_PTH'))
    bld_path = Path(os.getenv('BLD_PTH'))
    bld_lyr_nme = os.getenv('BLD_LYR_NME')
    
    out_gpkg = Path(os.getenv('OUT_GPKG'))
    out_bld_lyr_nme = os.getenv('OUT_BLD_LYR_NME')
    out_pcl_lyr_nme = os.getenv('PCL_LYR_NME')

    # Load in the data
    aoi_mask = gpd.read_file(aoi_path, layer=aoi_lyr_nme)

    bld_gdf = gpd.read_file(bld_path, layer=bld_lyr_nme, mask=aoi_mask)
    bld_gdf = bld_gdf[bld_gdf.geometry != None]
    
    cut_gdf = gpd.read_file(parcel_path, mask=aoi_mask)
    cut_gdf = cut_gdf[cut_gdf.geometry != None]

    print('cutting buildings')
    clipped_polys = PolygonCutter(bld_poly=bld_gdf, cut_geom=cut_gdf)
    clipped_polys.bp.to_file(out_gpkg, layer=out_bld_lyr_nme)
    clipped_polys.line_geom.to_file(out_gpkg, layer='poly_lines')


if __name__ == '__main__':
    main()
    print('DONE!')
