import geopandas as gpd
import numpy as np
import os
import pandas as pd
import sys
from shapely.geometry import Point
from pathlib import Path
from dotenv import load_dotenv
from operator import itemgetter
sys.path.insert(1, os.path.join(sys.path[0], ".."))
import datetime

'''

This script attempts to take building footprints and match them to an address point. Multiple copies of an address 
points created if there are multiple buildings but only 1 address point. In cases where there are many buildings
and many address points a copy of each address point will be placed on each building unless otherwise stated. 

This script will return a point layer with each record containing a link to a building footprint. 

Unlinked addresses and buildings will then be output for further analysis.

'''

class Matcher:
    '''Creates a matched output from the provided inputs'''
    def __init__(self, address_data_path:str, footprint_data_path:str, address_lyr_nme:str=None, footprint_lyr_nme:str=None, proj_crs:int=4326, footprint_join_field:str='link_field', address_join_field:str='link_field', shed_flag_field:str='shed_flag', bp_threshold:int=20, bp_area_threshold:int=20, buffer_size:int=20 ) -> None:
        
        def groupby_to_list(df:pd.DataFrame, group_field:str, list_field:str) -> pd.Series:
    
            """
            Helper function: faster alternative to pandas groupby.apply/agg(list).
            Groups records by one or more fields and compiles an output field into a list for each group.
            """
            
            if isinstance(group_field, list):
                for field in group_field:
                    if df[field].dtype.name != "geometry":
                        df[field] = df[field].astype("U")
                transpose = df.sort_values(group_field)[[*group_field, list_field]].values.T
                keys, vals = np.column_stack(transpose[:-1]), transpose[-1]
                keys_unique, keys_indexes = np.unique(keys.astype("U") if isinstance(keys, np.object) else keys, 
                                                    axis=0, return_index=True)
            
            else:
                keys, vals = df.sort_values(group_field)[[group_field, list_field]].values.T
                keys_unique, keys_indexes = np.unique(keys, return_index=True)
            
            vals_arrays = np.split(vals, keys_indexes[1:])
            
            return pd.Series([list(vals_array) for vals_array in vals_arrays], index=keys_unique).copy(deep=True)


        def building_area_theshold_id(building_gdf:gpd.GeoDataFrame, bf_area_threshold , area_field_name='bf_area') -> bool:
            '''
            Returns a boolean on whether a majority of the buildings in the bp fall under the bp threshold defined in the environments. 
            Buildings should be filtered to only those in the polygon before being passed into this function
            '''
            
            all_bf_cnt = len(building_gdf)

            bf_u_thresh = building_gdf[building_gdf[area_field_name] <= bf_area_threshold]
            bf_u_thresh_cnt = len(bf_u_thresh)

            if bf_u_thresh_cnt >= (all_bf_cnt/2):
                return True
            else:
                return False


        def get_unlinked_geometry(addresses_gdf:gpd.GeoDataFrame, footprint_gdf:gpd.GeoDataFrame , buffer_distance:int=20) -> gpd.GeoDataFrame:
            'Returns indexes for the bf based on the increasing buffer size'
            
            def list_bf_indexes(buffer_geom, bf_gdf):
                """
                For parcel-less bf geometry takes the buffer from the buffer_geom field and looks for 
                intersects based on the buffer geom. Returns a list of all indexes with true values.
                """
                intersects = bf_gdf.intersects(buffer_geom)
                intersects = intersects[intersects == True]
                intersects = tuple(intersects.index)
                if len(intersects) > 0:
                    return intersects
                else: 
                    return np.nan
    
            addresses_gdf['buffer_geom'] = addresses_gdf.geometry.buffer(buffer_distance)
            addresses_gdf[f'footprint_index'] = addresses_gdf['buffer_geom'].apply(lambda point_buffer: list_bf_indexes(point_buffer, footprint_gdf))

            linked_df = addresses_gdf.dropna(axis=0, subset=[f'footprint_index'])
            linked_df['method'] = f'{buffer_distance}m buffer'
            linked_df.drop(columns=["buffer_geom"], inplace=True)
            addresses_gdf = addresses_gdf[~addresses_gdf.index.isin(list(set(linked_df.index.tolist())))]
            return linked_df


        def get_nearest_linkage(ap:Point, footprint_indexes:list) -> list:
            """Returns the footprint index associated with the nearest footprint geometry to the given address point."""  
            # Get footprint geometries.
            footprint_geometries = tuple(map(lambda index: self.footprint["geometry"].loc[self.footprint.index == index], footprint_indexes))
            # Get footprint distances from address point.
            footprint_distances = tuple(map(lambda footprint: footprint.distance(ap), footprint_geometries))                                     
            distance_values = [a[a.index == a.index[0]].values[0] for a in footprint_distances if len(a.index) != 0]
            distance_indexes = [a.index[0] for a in footprint_distances if len(a.index) != 0]

            if len(distance_indexes) == 0: # If empty then return drop val
                return np.nan

            footprint_index =  distance_indexes[distance_values.index(min(distance_values))]
            return footprint_index


        def check_for_intersects(address_pt:Point, footprint_indexes: list) -> int:
            '''Similar to the get nearest linkage function except this looks for intersects (uses within because its much faster) and spits out the index of any intersect'''
            footprint_geometries = tuple(map(lambda index: self.footprint["geometry"].loc[self.footprint.index == index], footprint_indexes))
            inter = tuple(map(lambda bf: address_pt.within(bf.iloc[0]), footprint_geometries))
            if True in inter:
                t_index = inter.index(True)
                return int(footprint_geometries[t_index].index[0])


        def as_int(val):
            "Step 4: Converts linkages to integer tuples, if possible"
            try:
                if isinstance(val, int):
                    return val
                else:
                    return int(val)
            except ValueError:
                return val


        def create_centroid_match(footprint_index:int, bf_centroids):
            '''Returns the centroid geometry for a given point'''
            new_geom = bf_centroids.iloc[int(footprint_index)]
            return new_geom


        # Step 1: Import key layers and reproject them
        self.addresses = gpd.read_file(address_data_path, layer=address_lyr_nme, crs=proj_crs)
        self.footprint = gpd.read_file(footprint_data_path, layer=footprint_lyr_nme, crs=proj_crs)

        self.addresses.to_crs(crs=proj_crs, inplace=True)
        self.footprint.to_crs(crs=proj_crs, inplace=True)

        # Step 2: Configure address to footprint linkages
        self.addresses["addresses_index"] = self.addresses.index
        self.footprint["footprint_index"] = self.footprint.index

        # Remove buildings flagged as sheds as they do not need to be matched
        self.sheds = self.footprint[self.footprint[shed_flag_field] == True] # Set aside for use in future if sheds need to be matched
        self.footprint = self.footprint[self.footprint[shed_flag_field] == False]

        print('     creating and grouping linkages')
        merge = self.addresses[~self.addresses[address_join_field].isna()].merge(self.footprint[[footprint_join_field, "footprint_index"]], how="left", left_on=address_join_field, right_on=footprint_join_field)
        self.addresses['footprint_index'] = groupby_to_list(merge, "addresses_index", "footprint_index")
        self.addresses.drop(columns=["addresses_index"], inplace=True)

        # Big Parcel (BP) case extraction (remove and match before all other cases
        bf_counts = self.footprint.groupby(footprint_join_field, dropna=True)[footprint_join_field].count()
        ap_counts = self.addresses.groupby(address_join_field, dropna=True)[address_join_field].count()

        # Take only parcels that have more than the big parcel (bp) threshold intersects of both a the inputs
        addresses_bp = self.addresses.loc[(self.addresses[address_join_field].isin(bf_counts[bf_counts > bp_threshold].index.tolist())) & (self.addresses[address_join_field].isin(ap_counts[ap_counts > bp_threshold].index.tolist()))]
        
        if len(addresses_bp) > 0:
            # return all addresses with a majority of the buildings under the area threshold
            addresses_bp['u_areaflag'] = addresses_bp['footprint_index'].apply(lambda x: building_area_theshold_id(self.footprint[self.footprint['footprint_index'].isin(x)], bp_area_threshold)) 
            addresses_bp = addresses_bp.loc[addresses_bp['u_areaflag'] == True]
            addresses_bp.drop(columns=['u_areaflag'], inplace=True)

            self.addresses =  self.addresses[~self.addresses.index.isin(addresses_bp.index.tolist())]
            addresses_bp = get_unlinked_geometry(addresses_bp, self.footprint, buffer_distance=buffer_size)

            # Find and reduce plural linkages to the closest linkage
            ap_bp_plural = addresses_bp['footprint_index'].map(len) > 1
            addresses_bp.loc[ap_bp_plural, "footprint_index"] = addresses_bp[ap_bp_plural][["geometry", "footprint_index"]].apply(lambda row: get_nearest_linkage(*row), axis=1)
            addresses_bp.loc[~ap_bp_plural, "footprint_index"] = addresses_bp[~ap_bp_plural]["footprint_index"].map(itemgetter(0))
            addresses_bp['method'] = addresses_bp['method'].astype(str) + '_bp'
            addresses_bp['method'] = addresses_bp['method'].str.replace(' ','_')
        
        # Extract non-linked addresses if any.
        print('     extracting unlinked addresses')
        addresses_na = self.addresses[self.addresses['footprint_index'].isna()] # Special cases with NaN instead of a tuple
        self.addresses = self.addresses[~self.addresses.index.isin(addresses_na.index.tolist())]

        unlinked_aps = self.addresses[self.addresses["footprint_index"].map(itemgetter(0)).isna()] # Extract unlinked addresses
        if len(addresses_na) > 0:    
            unlinked_aps = unlinked_aps.append(addresses_na) # append unlinked addresses to the addresses_na

        # Separate out for the buffer phase
        # Discard non-linked addresses.
        self.addresses.drop(self.addresses[self.addresses["footprint_index"].map(itemgetter(0)).isna()].index, axis=0, inplace=True)

        print('Running Step 3. Checking address linkages via intersects')

        self.addresses['intersect_index'] = self.addresses[["geometry", "footprint_index"]].apply(lambda row: check_for_intersects(*row), axis=1)
        # Clean footprints remove none values and make sure that the indexes are integers
        intersections = self.addresses.dropna(axis=0, subset=['intersect_index'])

        self.addresses = self.addresses[self.addresses.intersect_index.isna()] # Keep only address points that were not intersects
        self.addresses.drop(columns=['intersect_index'], inplace=True) # Now drop the now useless intersects_index column

        intersect_a_points = list(set(intersections.intersect_index.tolist()))

        self.addresses.dropna(axis=0, subset=['footprint_index'], inplace=True)

        intersections['intersect_index'] = intersections['intersect_index'].astype(int)

        intersect_indexes = list(set(intersections.index.tolist()))

        intersections['footprint_index'] = intersections['intersect_index']
        intersections.drop(columns='intersect_index', inplace=True)
        intersections['method'] = 'intersect'

        # footprint = footprint[~footprint.index.isin(list(set(intersections.footprint_index.tolist())))] # remove all footprints that were matched in the intersection stage
        print('Running Step 4. Creating address linkages using linking data')

        # Ensure projected crs is used
        intersections.to_crs(crs=proj_crs, inplace=True)
        self.addresses.to_crs(crs= proj_crs, inplace=True)
        self.footprint.to_crs(crs=proj_crs, inplace=True)

        # Convert linkages to integer tuples, if possible.
        self.addresses["footprint_index"] = self.addresses["footprint_index"].map(lambda vals: tuple(set(map(as_int, vals))))

        # Flag plural linkages.
        flag_plural = self.addresses["footprint_index"].map(len) > 1
        self.addresses = self.addresses.explode('footprint_index') # Convert the lists into unique rows per building linkage (cleaned up later)

        self.addresses = self.addresses[self.addresses['footprint_index'] != np.nan]
        self.addresses['method'] = 'data_linking'

        # Get linkages via buffer if any unlinked data is present
        print('     get linkages via buffer')
        if len(unlinked_aps) > 0:
            
            unlinked_aps.to_crs(proj_crs, inplace=True)
            unlinked_aps.drop(columns=['footprint_index'], inplace=True)

            # split into two groups = points linked to a parcel - run against full building dataset, points with no footprint - only run against unlinked buildings
            no_parcel = unlinked_aps[unlinked_aps['link_field'].isna()]
            parcel_link = unlinked_aps[~unlinked_aps['link_field'].isna()]

            # get all footprint_indexes (fi) from the previous steps to exclude in the next step for no parcel aps
            intersect_fi = list(set(intersections.footprint_index.tolist()))
            linking_fi = list(set(self.addresses.footprint_index.tolist()))

            # Bring in only those footprints that haven't yet been matched to remove matches on buildings already matched
            unlinked_footprint = self.footprint[~(self.footprint['footprint_index'].isin(linking_fi) | self.footprint['footprint_index'].isin(intersect_fi))]

            print('     processing unlinked geometry')
            # run the next line using only the footprints that are not already linked to an address point
            no_parcel = get_unlinked_geometry(no_parcel, unlinked_footprint, buffer_size)
            parcel_link = get_unlinked_geometry(parcel_link, self.footprint, buffer_size)
            
            # Grab those records that still have no link and export them for other analysis
            unmatched_points = unlinked_aps[~((unlinked_aps.index.isin(list(set(no_parcel.index.to_list())))) | (unlinked_aps.index.isin(list(set(parcel_link.index.to_list())))))]
            print(f'Number of unlinked addresses {len(unmatched_points)}')
            
            unlinked_aps = no_parcel.append(parcel_link)
            # Take only the closest linkage for unlinked geometries
            unlinked_plural = unlinked_aps['footprint_index'].map(len) > 1
            unlinked_aps.loc[unlinked_plural, "footprint_index"] = unlinked_aps[unlinked_plural][["geometry", "footprint_index"]].apply(lambda row: get_nearest_linkage(*row), axis=1)
            unlinked_aps = unlinked_aps.explode('footprint_index')
            unlinked_aps['method'] = f'{buffer_size}m_buffer'

        print("Running Step 5. Merge and Export Results")

        self.outgdf = self.addresses.append([intersections, addresses_bp, unlinked_aps])

        print("Running Step 6: Change Point Location to Building Centroid")
        print('     Creating footprint centroids')
        self.footprint['centroid_geo'] = self.footprint['geometry'].apply(lambda bf: bf.representative_point())
        print('     Matching address points with footprint centroids')
        self.outgdf['out_geom'] = self.outgdf['footprint_index'].apply(lambda row: create_centroid_match(row, self.footprint['centroid_geo']))

        self.outgdf = self.outgdf.set_geometry('out_geom')

        self.outgdf.drop(columns='geometry', inplace=True)
        self.outgdf.rename(columns={'out_geom':'geometry'}, inplace=True)
        self.outgdf = self.outgdf.set_geometry('geometry')

        self.footprint.drop(columns='centroid_geo', inplace=True)

        # Find unlinked building polygons
        self.unlinked_footprint = self.footprint[~self.footprint['footprint_index'].isin(self.outgdf['footprint_index'].to_list())]


    def export_matches(self, output_gpkg:str, matches_lyr_nme:str='matched_points', sheds_lyr_nme:str='non_add_ob') -> None:
        self.outgdf.to_file(output_gpkg, layer=matches_lyr_nme)
        self.sheds.to_file(output_gpkg, layer=sheds_lyr_nme)
        self.unlinked_footprint.to_file(output_gpkg, layer=sheds_lyr_nme)


def main():
    load_dotenv(os.path.join(os.path.dirname(__file__), 'NWT_environments.env'))

    output_path = os.getcwd()
    output_gpkg = Path(os.getenv('MATCHED_OUTPUT_GPKG'))
    matched_lyr_nme = os.getenv('MATCHED_OUTPUT_LYR_NME')
    unmatched_lyr_nme = os.getenv('UNMATCHED_OUTPUT_LYR_NME')
    unmatched_poly_lyr_nme = os.getenv('UNMATCHED_POLY_LYR_NME')

    # Layer inputs cleaned versions only
    project_gpkg = Path(os.getenv('DATA_GPKG'))
    footprints_lyr_nme = os.getenv('CLEANED_BF_LYR_NAME')
    addresses_lyr_nme = os.getenv('FLAGGED_AP_LYR_NME')

    proj_crs = int(os.getenv('PROJ_CRS'))

    add_num_fld_nme =  os.getenv('AP_CIVIC_ADDRESS_FIELD_NAME')
    unlinked_bf_lyr_nme = os.getenv('UNLINKED_BF_LYR_NME')

    out_lyr_nme = os.getenv('LINKED_BY_DATA_NME')

    buffer_size = 20 # distance for the buffer

    metrics_out_path = Path(os.getenv('METRICS_CSV_OUT_PATH'))

    bp_threshold = int(os.getenv('BP_THRESHOLD'))
    bp_area_threshold = int(os.getenv('BP_AREA_THRESHOLD'))
    
    matches = Matcher(project_gpkg, 
                    project_gpkg, 
                    addresses_lyr_nme, 
                    footprints_lyr_nme,
                    )
    matches.export_matches(output_gpkg, out_lyr_nme)

if __name__ == '__main__':
    main()
