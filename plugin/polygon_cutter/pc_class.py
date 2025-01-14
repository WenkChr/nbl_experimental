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


class PC:
    
    '''
    Splits an input polygon where it intersects the input cut geometry. Cut geometry must be polygon or lines no points accepted. 
    Output geometry is then cleaned to keep only valid splits

    Inputs:

    bld_poly: building polygons to be split
    
    cut_geom: the geometry (lines or polygons) that will be used to split the bld_poly data
    
    point_data: path to point data that can be used to filter the cut_geom via intersection

    crs: geographic crs for the data

    proj_crs: projected crs will be the crs of the output layers

    sliver_max_area: the maximum area of a split polygon that will be defined as a sliver and not included in the output  
    '''

    def __init__(self, bld_poly: gpd.GeoDataFrame, cut_geom: gpd.GeoDataFrame, point_data=None, crs=4326, proj_crs=32614, sliver_max_area=20) -> None:
        
        def reproject(ingdf: gpd.GeoDataFrame, output_crs: int) -> gpd.GeoDataFrame:
            ''' Takes a gdf and tests to see if it is in the projects crs if it is not the funtions will reproject '''
            if ingdf.crs == None:
                ingdf.set_crs(epsg=output_crs, inplace=True)    
            elif ingdf.crs != f'epsg:{output_crs}':
                ingdf.to_crs(epsg=output_crs, inplace=True)
            return ingdf

        
        def ValidateGeometry(input_geometry: shapely.geometry) -> gpd.GeoSeries:
            '''Checks if input geometry is valid and if invalid attempts to make it valid accepts Geodataframes and Geoseries'''
            if type(input_geometry) == gpd.GeoSeries:
                input_geometry = input_geometry.apply(lambda geom: make_valid(geom))
            if type(input_geometry) == gpd.GeoDataFrame:
                input_geometry = input_geometry['geometry'].apply(lambda geom: make_valid(geom) if not geom.is_valid else geom)
            return input_geometry


        def ToSingleLines(geom: shapely.geometry) -> MultiLineString:
            '''Converts polygons into single lines'''
                
            def MultiLineDevolver(m_line_string: MultiLineString) -> list:
                '''Converts a multilinestring into a list of its component lines'''
                m_line_string = [l for l in m_line_string.geoms]
                m_line_string = [list(map(LineString, zip(l.coords[:-1], l.coords[1:]))) for l in m_line_string]
                m_line_string = [ls for l in m_line_string for ls in l]
                return m_line_string

            # temp measure to remove GeometryCollections and None cases
            if geom.geom_type not in ['MultiPolygon', 'Polygon', 'LineString', 'MultiLineString', 'Point', 'MultiPoint']:
                # Temp block in place until a solution is found for GeometryCollections
                print(geom)
                sys.exit()
                return None
                

            if geom.geom_type in ['LineString', 'MultiLineString']:
                # If linestring type then no need to worry about boundaries
                if geom.geom_type == 'MultiLineString':
                    # Extra step for multi line strings need to be devolved first
                    geom = MultiLineDevolver(geom)
                    return geom
                
                # LineStrings get converted into a list of lines in a single step
                lines = list(map(LineString, zip(geom.coords[:-1], geom.coords[1:])))
                return lines
                


            if geom.geom_type in ['Polygon', 'MultiPolygon']:

                bounds = geom.boundary # returns the boundary as a linestring of many lines. Need to cut this into individual lines
                
                # multilinestrings need to be handled slightly differently
                if bounds.geom_type == 'MultiLineString':
                    # Extract each line from a multilinestring and return as a list of singe line linestrings
                    # inefficient way to do this but need to extract each line from a multilinestring perhaps look at this again later 
                    bounds = MultiLineDevolver(bounds)
                    return bounds

                # if its just a single line string then deconstruct to single geometries
                try:
                    line_bounds = list(map(LineString, zip(bounds.coords[:-1], bounds.coords[1:])))
                    return line_bounds
                
                except NotImplementedError: # Not implimented error can't break down multipart geometry using this method
                    print(bounds)
                    sys.exit()


        def SwapGeometry(ingdf: gpd.GeoDataFrame, orig_geom: str, swap_geom:str) -> gpd.GeoDataFrame:
            '''Utility function swap from one geometry field to another and drop the original geometries and renames the new geometry column to 'geometry'
            for the purposes of standardization'''
            
            # Swap in the new geometry
            ingdf = ingdf.set_geometry(swap_geom)
            # Delete the old geometry
            ingdf.drop(columns=[orig_geom], inplace=True)
            # Rename the geometry column to 'geometry'
            ingdf.rename({swap_geom:'geometry'}, axis='columns', inplace=True)
            # Ensure the column is set correctly by setting geometry again
            ingdf = ingdf.set_geometry('geometry')
            
            return ingdf


        def check_geom(input_gdf: gpd.GeoDataFrame, geometry_column= 'geometry') -> gpd.GeoDataFrame:
            '''Checks to see if the input  geometry is a line. If polygon converts to lines. If points or other returns a geometry error'''                         

            #input_gdf.reset_index(inplace=True)
            if input_gdf.geometry[0].geom_type in ['LineString', 'MultiLineString']:
                # If the geometry is already in line type
                return input_gdf
            
            # If inputs are polygons then convert them to lines
            if input_gdf.geometry[0].geom_type in ['Polygon', 'MultiPolygon']:
                
                # explode to remove multipolygons
                input_gdf = input_gdf.explode(index_parts=False)
                # convert linestrings into single linestrings 
                input_gdf['single_lines'] = input_gdf['geometry'].swifter.apply(lambda p: ToSingleLines(p))
                # explode list output of prior function
                output_gdf = input_gdf.explode('single_lines')
                # switch geometry to the new geom and drop old geom
                output_gdf = SwapGeometry(output_gdf, 'geometry', 'single_lines')

                return output_gdf

            # If the geometry is a point or mutipoint raise an error
            if input_gdf.geometry[0].geom_type in ['Point', 'MultiPoint']:
                raise IOError('Shape is not a Polygon or Line')


        def FindIntersects(input_geom: gpd.GeoDataFrame, search_geometry: gpd.GeoDataFrame, input_link_field: str, search_link_field: str) -> gpd.GeoDataFrame:
            '''finds all intersections between the input geometry and the search geometry'''

            joined_geom = gpd.sjoin(input_geom, search_geometry[[search_link_field, 'geometry']], op='intersects')
            input_geom['line_ints'] = input_geom[input_link_field].swifter.apply(lambda x: tuple(joined_geom[joined_geom[input_link_field] == x][search_link_field].tolist()))
            return input_geom


        def CutPolygon(intersect_indexes: tuple, in_geom: Polygon, line_geom:gpd.GeoDataFrame, cut_field:str) -> MultiPolygon:
            '''Cuts the input polygon by the lines linked to it during the FindIntersects Step Run the FindIntersects step before calling this function'''
           
            # Select only key vars and set the cut indexes
            line_geom = line_geom[[cut_field, 'geometry']]
            cut_indexes = intersect_indexes

            # Polygons with no intersects don't need to be split
            if len(cut_indexes) == 0:
                return in_geom
            
            # Polygons with intersects need to be split
            if len(cut_indexes) >= 1:
                # retrieve the records related to the cut indexes
                cutters = line_geom[line_geom[cut_field].isin(cut_indexes)]
                
                # convert to a single LineString or MultiLineString
                cut_single = [shapely.ops.linemerge(cutters.geometry.values.tolist())]
                
                # Convert the polygon into its boundary and append it to the cut lines list
                cut_single.append(in_geom.boundary)
                # Create a union between all the lines
                cut_single = shapely.ops.unary_union(cut_single)
                # merge all the lines into a single LineString or MultiLineString
                cut_single = shapely.ops.linemerge(cut_single)
                # Convert the linemerge result back into a polygon
                polygons = shapely.ops.polygonize(cut_single)
                # Ensure result is a MultiPolygon and return it
                return MultiPolygon(polygons)

 
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
        
        # if points are available filter out polygons that do not intersect with a point
        if type(point_data) == gpd.GeoDataFrame:

            point_data.to_crs(crs=crs, inplace=True)
            point_data['ap_index'] = range(1, len(point_data.index) + 1)
            cut_joined_ap = gpd.sjoin(cut_geom, point_data[['ap_index', 'geometry']])
            cut_joined_ap = list(set(cut_joined_ap[~cut_joined_ap['ap_index'].isna()]['cut_index'].tolist()))
            cut_geom = cut_geom[cut_geom['cut_index'].isin(cut_joined_ap)]

        # convert the cut geometry to lines if necessary
        print('Converting line geometry')

        self.line_geom = check_geom(cut_geom)
        # self.line_geom = self.line_geom[self.line_geom['geometry'] != None]
        # self.line_geom.reset_index(inplace=True)
        
        # ensure crs is consistent after the geometry change
        self.line_geom.set_crs(crs=crs, inplace=True)
        # Create a new index for the lines
        self.line_geom['line_index'] = range(1, len(self.line_geom.index) + 1)
        
        # Commented out as this leads to complex multiline situations. Keeping these lines in prevents that problem
        #Drop lines that do not intersect a building
        lines_joined = gpd.sjoin(self.line_geom, self.bp[['bp_index', 'geometry']])
        self.line_geom = self.line_geom[self.line_geom['line_index'].isin(list(set(lines_joined[~lines_joined['bp_index'].isna()]['line_index'].tolist())))]
        
        # Project data for overlap checks
        self.line_geom = reproject(self.line_geom, proj_crs)
        self.bp = reproject(self.bp, proj_crs)

        # Delete lines that overlap
        self.line_geom.reset_index(drop=True, inplace=True)

        # Calc centroid for duplicate removal (lines don't work for this method)
        self.line_geom['centroid'] = self.line_geom.geometry.centroid
        # convert to wkb because drop duplicates doesn't work on shapely
        self.line_geom['centroid'] = self.line_geom['centroid'].apply(lambda geom: geom.wkb)

        self.line_geom = self.line_geom.drop_duplicates(['centroid']) # Drop the duplicate records

        # convert back to shapely geometry (only necessary when using that geometry)
        #self.line_geom['centroid'] = self.line_geom['centroid'].apply(lambda geom: shapely.wkb.loads(geom))

        # Drop non essential centroid field
        self.line_geom.drop(columns=['centroid'], inplace=True)
        
        # Check for and exclude non line geometries

        self.line_geom['geom_type'] = self.line_geom.geometry.geom_type
        self.line_geom = self.line_geom[self.line_geom['geom_type'].isin(['LineString', 'MultiLineString'])]
        self.line_geom.drop(columns=['geom_type'], inplace=True)

        # Remove multilinestrings by merging lines if possible
        self.line_geom['singled_geom'] = self.line_geom['geometry'].apply(lambda x: shapely.ops.linemerge(x) if x.geom_type == 'MultiLineString' else x)
             
        self.line_geom = SwapGeometry(self.line_geom, 'geometry', 'singled_geom')
        
        # if any multilinestrings remain explode them as split cannot take multi geometry
        multis = self.line_geom[self.line_geom.geometry.geom_type == 'MultiLineString']
        
        # if there are still multipolygons then use the following to remove them
        if len(multis) > 0:
            self.line_geom = self.line_geom[self.line_geom.geometry.geom_type != 'MultiLineString']
            # explode and add the now single linestrings back into the data
            multis = multis.explode(index_parts=False)
            self.line_geom = self.line_geom.append(multis)
        
        self.line_geom['seg_index'] = range(1, len(self.line_geom.index) + 1)
        
        print('Finding intersects') 
        self.line_geom = reproject(self.line_geom, proj_crs)
        self.bp = reproject(self.bp, proj_crs)

        self.bp = FindIntersects(self.bp, self.line_geom, 'bp_index', 'seg_index')

        print('Cutting by intersects')
        
        # Cut the polygons
        cut_geom = self.bp[['line_ints', 'geometry']].swifter.apply(lambda x: CutPolygon(x.line_ints, x.geometry, self.line_geom[['seg_index', 'geometry']], 'seg_index'), axis=1)

        self.bp['geometry'] = cut_geom
        self.bp = self.bp.explode(index_parts=True)
        self.bp.drop(columns=['line_ints'], inplace=True)
        
        # Final split buildings crs check
        self.line_geom = reproject(self.line_geom, proj_crs)
        self.bp = reproject(self.bp, proj_crs)

        # Clean up results and remove slivers polygons with an area less than the max sliver area
        self.bp['split_area'] = round(self.bp.geometry.area, 2)

        self.slivers = self.bp[self.bp.split_area <= sliver_max_area] # retain slivers for analysis purposes if needed
        self.bp = self.bp[self.bp.split_area >= sliver_max_area]

        # Drop temp fields
        self.bp.drop(columns=['split_area', 'bp_index'], inplace=True)
        self.line_geom.drop(columns=['cut_index', 'line_index', 'seg_index'], inplace=True)
          
        
    def __call__(self, *args, **kwds):
        pass

def main():
    # setup for testing purposes
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), 'cutting.env'))

    aoi_path = Path(os.getenv('AOI_TEST_AREA'))
    aoi_lyr_nme = os.getenv('AOI_TEST_LYR_NME')

    parcel_path = Path(os.getenv('PARCEL_PTH'))
    bld_path = Path(os.getenv('BLD_PTH'))
    bld_lyr_nme = os.getenv('BLD_LYR_NME')
    ap_data = os.getenv('AP_DATA')
    
    out_gpkg = Path(os.getenv('OUT_GPKG'))
    out_bld_lyr_nme = os.getenv('OUT_BLD_LYR_NME')
    out_pcl_lyr_nme = os.getenv('PCL_LYR_NME')

    # Load in the data
    aoi_mask = gpd.read_file(aoi_path, layer=aoi_lyr_nme)

    bld_gdf = gpd.read_file(bld_path, layer=bld_lyr_nme, mask=aoi_mask)
    bld_gdf = bld_gdf[bld_gdf.geometry != None]
    
    cut_gdf = gpd.read_file(parcel_path, mask=aoi_mask)
    cut_gdf = cut_gdf[cut_gdf.geometry != None]

    addresses = gpd.read_file(ap_data)

    print('cutting buildings')
    clipped_polys = PolygonCutter(bld_poly=bld_gdf, cut_geom=cut_gdf, point_data=addresses)
    clipped_polys.bp.to_file(out_gpkg, layer=out_bld_lyr_nme)
    clipped_polys.line_geom.to_file(out_gpkg, layer=out_pcl_lyr_nme)


if __name__ == '__main__':
    main()
    print('DONE!')
