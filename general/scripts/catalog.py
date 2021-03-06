#!python3
import xml.etree.ElementTree as ET
import aiofiles
import asyncio
import asyncpg
import os
import sys
import random
import string
import logging
import argparse
import configparser


logging.basicConfig(stream=sys.stdout,
                    level=logging.INFO,
                    format='[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s')


async def db_moasic_upsert(conn, row):
    mosaic_id = await conn.fetchrow('INSERT INTO emucat.mosaics ("ser_id", "table_version", "image_file", '
                                     '"flag_subsection",'
                                     '"subsection", "flag_statsec", "statsec", "search_type", "flag_negative", '
                                     '"flag_baseline", "flag_robuststats", "flag_fdr", "threshold", "flag_growth", '
                                     '"growth_threshold", "min_pix", "min_channels", "min_voxels", "flag_adjacent", '
                                     '"thresh_velocity", "flag_rejectbeforemerge", "flag_twostagemerging", '
                                     '"pixel_centre", "flag_smooth", "flag_atrous", "reference_frequency", '
                                     '"threshold_actual") '
                                     'VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,'
                                     '$16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27) '
                                     'ON CONFLICT ("ser_id", "subsection") '
                                     'DO UPDATE SET subsection = EXCLUDED.subsection RETURNING id',
                                     *row)
    return mosaic_id[0]


async def db_components_upsert_many(conn, rows):
    await conn.executemany('INSERT INTO emucat.components ("mosaic_id","island_id","component_id",'
                                     '"component_name","ra_hms_cont","dec_hms_cont","ra_deg_cont",'
                                     '"dec_deg_cont","ra_err","dec_err","freq","flux_peak","flux_peak_err",'
                                     '"flux_int","flux_int_err","maj_axis","min_axis","pos_ang","maj_axis_err",'
                                     '"min_axis_err","pos_ang_err","maj_axis_deconv","min_axis_deconv",'
                                     '"pos_ang_deconv","maj_axis_deconv_err","min_axis_deconv_err",'
                                     '"pos_ang_deconv_err","chi_squared_fit","rms_fit_gauss","spectral_index",'
                                     '"spectral_curvature","spectral_index_err","spectral_curvature_err",'
                                     '"rms_image","has_siblings","fit_is_estimate","spectral_index_from_tt",'
                                     '"flag_c4","comment")'
                                     'VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,'
                                     '$16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29,'
                                     '$30, $31, $32, $33, $34, $35, $36, $37, $38, $39) '
                                     'ON CONFLICT ("mosaic_id", "component_name", "ra_deg_cont", "dec_deg_cont") DO NOTHING',
                                     rows)


async def db_lhr_upsert_many(conn, rows):
    await conn.executemany('INSERT INTO emucat.sources_lhr_allwise ("component_id", "wise_id", "w1_LR",'
                                     '"w1_Rel","w1_n_cont","w1_separation")'
                                     'VALUES($1, $2, $3, $4, $5, $6) '
                                     'ON CONFLICT ("component_id", "wise_id") DO NOTHING',
                                     rows)


async def _get_file_bytes(path: str, mode: str = 'rb'):
    buffer = []

    async with aiofiles.open(path, mode) as f:
        while True:
            buff = await f.read()
            if not buff:
                break
            buffer.append(buff)
        if 'b' in mode:
            return b''.join(buffer)
        else:
            return ''.join(buffer)


def convert(value, datatype):
    if datatype == 'float':
        return float(value)
    elif datatype =='boolean':
        return bool(int(value))
    elif datatype == 'int':
        return int(value)
    elif datatype == 'double':
        return float(value)
    return value


async def import_selavy_catalog(conn, ser_name: str, filename: str):
    ser_id = await conn.fetchrow('SELECT id from emucat.source_extraction_regions where name=$1', ser_name)
    if not ser_id:
        raise Exception('Source extraction region not found')

    ser_id = ser_id[0]

    ns = {'ivoa': 'http://www.ivoa.net/xml/VOTable/v1.3'}

    content = await _get_file_bytes(filename, mode='r')
    root = ET.fromstring(content)

    mosaic_map = {'ser_id': None,
                'table_version': None,
                'imageFile': None,
                'flagSubsection': False,
                'subsection': None,
                'flagStatSec': False,
                'StatSec': None,
                'searchType': None,
                'flagNegative': False,
                'flagBaseline': False,
                'flagRobustStats': False,
                'flagFDR': False,
                'threshold': None,
                'flagGrowth': False,
                'growthThreshold': 0.0,
                'minPix': 0,
                'minChannels': 0,
                'minVoxels': 0,
                'flagAdjacent': False,
                'threshVelocity': None,
                'flagRejectBeforeMerge': False,
                'flagTwoStageMerging': False,
                'pixelCentre': None,
                'flagSmooth': False,
                'flagATrous': False,
                'Reference frequency': 0.0,
                'thresholdActual': 0.0}

    for param in root.findall('./ivoa:RESOURCE/ivoa:TABLE/ivoa:PARAM', ns):
        key = param.get('name')
        value = convert(param.get('value'), param.get('datatype'))
        if key == 'imageFile':
            value = os.path.basename(value)
        mosaic_map[key] = value


    mosaic_map['ser_id'] = ser_id
    params = list(mosaic_map.values())
    mosaic_id = await db_moasic_upsert(conn, params)

    datatypes = []
    for field in root.findall('./ivoa:RESOURCE/ivoa:TABLE/ivoa:FIELD', ns):
        datatypes.append(field.get('datatype'))

    rows = []
    for i, tr in enumerate(root.findall('./ivoa:RESOURCE/ivoa:TABLE/ivoa:DATA/ivoa:TABLEDATA/ivoa:TR', ns)):
        cat = [convert(td.text.strip(), datatypes[j]) for j, td in enumerate(tr)]
        cat.insert(0, mosaic_id)
        rows.append(cat)

    await db_components_upsert_many(conn, rows)


async def import_lhr_catalog(conn, filename: str):
    ns = {'ivoa': 'http://www.ivoa.net/xml/VOTable/v1.4'}

    content = await _get_file_bytes(filename, mode='r')
    root = ET.fromstring(content)

    datatypes = []
    for field in root.findall('./ivoa:RESOURCE/ivoa:TABLE/ivoa:FIELD', ns):
        datatypes.append(field.get('datatype'))

    rows = []
    for i, tr in enumerate(root.findall('./ivoa:RESOURCE/ivoa:TABLE/ivoa:DATA/ivoa:TABLEDATA/ivoa:TR', ns)):
        cat = [convert(td.text.strip(), datatypes[j]) for j, td in enumerate(tr)]
        rows.append(cat)

    await db_lhr_upsert_many(conn, rows)


def import_lhr(args):
    asyncio.run(import_lhr_votable(args.input, args.credentials))


async def import_lhr_votable(filename: str, credentials: str):
    config = configparser.ConfigParser()
    config.read(credentials)
    user = config['emucat_database']['user']
    password = config['emucat_database']['password']
    database = config['emucat_database']['database']
    host = config['emucat_database']['host']

    conn = await asyncpg.connect(user=user, password=password, database=database, host=host)
    await import_lhr_catalog(conn, filename)


def import_selavy(args):
    asyncio.run(import_selavy_votable(args.ser, args.input, args.credentials))


async def import_selavy_votable(ser_name: str, filename: str, credentials: str):
    config = configparser.ConfigParser()
    config.read(credentials)
    user = config['emucat_database']['user']
    password = config['emucat_database']['password']
    database = config['emucat_database']['database']
    host = config['emucat_database']['host']

    conn = await asyncpg.connect(user=user, password=password, database=database, host=host)
    await import_selavy_catalog(conn, ser_name, filename)


def random_word(length):
   letters = string.ascii_lowercase
   return ''.join(random.choice(letters) for i in range(length))


async def import_allwise_catalog_from_csv(input_path: str):
    conn = await asyncpg.connect(user='admin', password='admin', database='emucat', host='localhost')
    async with conn.transaction():
        with open(input_path, 'rb') as f:
            table_name = random_word(6)
            temp_sql = f'CREATE TEMP TABLE {table_name} ON COMMIT DROP AS SELECT * FROM emucat.allwise WITH NO DATA;'
            await conn.fetchrow(temp_sql)
            copy_result = await conn.copy_to_table(table_name=table_name, source=f, format='csv')
            print(copy_result)
            copy_sql = f'INSERT INTO emucat.allwise SELECT * FROM {table_name} ORDER BY (designation) ' \
                       f'ON CONFLICT (designation) DO NOTHING'
            await conn.fetchrow(copy_sql)


def main():
    parser = argparse.ArgumentParser(prog='EMUCat', description='EMUCat catalog functions.')
    subparsers = parser.add_subparsers(help='sub-command help')

    input_selavy_parser = subparsers.add_parser('import_selavy', help='Import selavy component catalog into EMUCat.')
    input_selavy_parser.add_argument('-s', '--ser', help='Source extraction region.', type=str, required=True)
    input_selavy_parser.add_argument('-i', '--input', help='Selavy votable.', type=str, required=True)
    input_selavy_parser.add_argument('-c', '--credentials', help='Credentials file.', required=True)
    input_selavy_parser.set_defaults(func=import_selavy)

    input_lhr_parser = subparsers.add_parser('import_lhr', help='Import lhr results into EMUCat.')
    input_lhr_parser.add_argument('-i', '--input', help='lhr votable.', type=str, required=True)
    input_lhr_parser.add_argument('-c', '--credentials', help='Credentials file.', required=True)
    input_lhr_parser.set_defaults(func=import_lhr)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
        exit(0)
    except Exception as e:
        logging.exception(e)
        exit(1)
