"""
Functions to convert a VOX_coords_mother.txt file into a voxel_coordinates.json file.
Adds grid_group and grid_loc fields. 

Run:
    python vox_mother_converter.py <subject> <out_file>
to create a voxel_coordinates.json file
"""
import re
import pandas as pd
import os
from collections import defaultdict
from .config import paths
from .json_cleaner import clean_json_dump
import json

class Contact(object):
    """
    Simple Contact class that's just a container to hold properties of the contact.
    """

    def __init__(self, contact_name=None, contact_num=None, coords=None, type=None, size=None):
        self.name = contact_name
        self.coords = coords
        self.fs_coords = None
        self.num = contact_num
        self.type = type
        self.grid_size = size
        self.grid_group = None
        self.jack_num = None
        self.grid_loc = None
    
    def to_dict(self):
        d =  {'name': self.name,
              'lead_group': self.grid_group,
              'lead_loc': self.grid_loc,
              'coordinate_spaces':{'ct_voxel': {'raw': self.coords}}
              }
        if self.fs_coords is not None:
            d['coordinate_spaces']['fs'] = {'raw': self.fs_coords}
        return d



def leads_to_dict(leads):
    """
    Converts a dictionary that contains Contact objects to a dictionary that can
    be output as JSON.
    Copies all contacts into a single-level dictionary
    :param leads: dictionary of form {lead_name: {contact_name1: contact1, contact_name2:contact2, ...}}
    :returns: dictionary of form {contact_name1: {contact properties...}, contact_name2: {contact properties}}
    """
    out_dict = {}
    out_dict['origin_ct'] = 'UNKNOWN'
    out_dict['leads'] = {}
    for lead_name, contacts in list(leads.items()):
        lead_dict = {}
        contact = list(contacts.values())[0]
        lead_dict['type'] = contact.type
        groups = set()
        contact_list = []
        for contact in list(contacts.values()):
            groups.add(contact.grid_group)
            contact_list.append(contact.to_dict())
        lead_dict['contacts'] = contact_list
        lead_dict['n_groups'] = len(groups)
        out_dict['leads'][lead_name] = lead_dict
    return out_dict

def read_mother(mother_file):
    """
    Reads VOX_coords_mother, returning a dictionary of leads
    :param mother_file: path to VOX_coords_mother.txt file
    :returns: dictionary of form {lead_name: {contact_name1: contact1, contact_name2:contact2, ...}}
    """
    # defaultdict allows dictionaries to be created on access so we don't have to check for existence
    leads = defaultdict(dict)
    mother_table = pd.read_csv(mother_file,sep='\t',header=None,
                               names=['contact','x','y','z','type','shape'],index_col=False)
    for line in mother_table.index:
        split_line = mother_table.loc[line]
        # import pdb
        # pdb.set_trace()
        contact_name = split_line['contact']

        # Get information from the file
        coords = [int(x) for x in split_line[['x','y','z']].values]
        type = split_line['type']
        size = tuple(int(x) for x in split_line['shape'].split())

        # Split out contact name and number
        match = re.match(r'(.+?)(\d+)([mM]*icro)*$', contact_name)
        lead_name = match.group(1)
        contact_num = int(match.group(2))

        # Enter into "leads" dictionary
        contact = Contact(contact_name, contact_num, coords, type, size)
        leads[lead_name][contact_num] = contact
    return leads

def add_jacksheet(leads, jacksheet_file):
    """
    Adds information from the jacksheet to the "leads" structure from VOX_coords_mother
    :param leads: dictionary of form {lead_name: {contact_name1: contact1, contact_name2:contact2, ...}}
    :param jacksheet_file: path to jacksheet.txt file
    :returns: Nothing. Modifies the leads dictionary in place
    """
    skipped_leads = []
    for line in open(jacksheet_file):
        split_line = line.split()
        jack_num = split_line[0]
        contact_name = split_line[1]
        # Tries to find lead name vs contact number
        match = re.match(r'(.+?)(\d+$)', contact_name)
        if not match:
            print(('Warning: cannot parse contact {}. Skipping'.format(contact_name)))
            continue
        lead_name = match.group(1)
        contact_num = int(match.group(2))

        # Makes sure the lead is localized in VOX_coords_mother (which is in leads structure)
        if lead_name not in leads:
            if lead_name not in skipped_leads:
                print(('Warning: skipping lead {}'.format(lead_name)))
                skipped_leads.append(lead_name)
            continue

        # Makes sure the contact in the lead is localized 
        if contact_num not in leads[lead_name]:
            print(('Error: neural lead {} missing contact {}'.format(lead_name, contact_num)))
            continue
        leads[lead_name][contact_num].jack_num = jack_num

def add_grid_loc(leads):
    """
    Adds the grid_loc and grid_group info to the leads structure
    :param leads: dictionary of form {lead_name: {contact_name1: contact1, contact_name2:contact2, ...}}
    :returns: Nothing. Modifies leads in place
    """
    for lead_name, lead in list(leads.items()):
        # Makes sure the contacts all have the same type
        types = set(re.match(r'u?(.)',contact.type).group(1) for contact in list(lead.values()))
        if len(types) > 1:
            raise Exception("Cannot convert lead with multiple types")
        type = types.pop()

        # Grid locations for strips and depths are just the contact number
        if type != 'G':
            for contact in list(lead.values()):
                contact.grid_loc = (1, contact.num)
                contact.grid_group = 0

        else:
            sorted_contact_nums = sorted(lead.keys())
            previous_grid_size = lead[sorted_contact_nums[0]].grid_size
            group = 0
            start_num = 1
            for num in sorted_contact_nums:
                contact = lead[num]
                # If the grid size changes, mark that appropriately
                if contact.grid_size != previous_grid_size:
                    group += 1
                    start_num = num
                # If the number is greater than the grid size, start a new grid group
                if num - start_num >= contact.grid_size[0] * contact.grid_size[1]:
                    group += 1
                    start_num = num
                # Add the grid_loc and group
                contact.grid_loc = ((num-start_num) % contact.grid_size[0], (num-start_num)/contact.grid_size[0])
                contact.grid_group = group

def x2_add_freesurfer_coords(leads, files):
    coords_file = files['fs_coords']
    for line in open(coords_file, 'r'):
        split_line = line.split('\t')
        contact_name = split_line[0][1:]
        fs_coords = [float(f) for f in split_line[1:4]]
        found = False
        for lead_name, lead in list(leads.items()):
            for contact in list(lead.values()):
                if contact.name == contact_name:
                    contact.fs_coords = fs_coords
                    found = True
                    break
            if found:
                break
        else:
            print(('WARNING: could not find {}'.format(contact_name)))

def X_add_freesurfer_coords(leads, files):
    raw_coords = files['fs_coords']
    for line in open(raw_coords, 'r'):
        split_line = line.split('\t')
        jack_num = int(split_line[0])
        fs_coords = [float(f) for f in split_line[1:]]
        for lead in list(leads.values()):
            for contact in list(lead.values()):
                if contact.jack_num is not None and int(contact.jack_num) == int(jack_num):
                    contact.fs_coords = fs_coords

def add_freesurfer_coords(leads, files):
    coords_file = files['fs_coords']
    child_file = files['vox_child']
    for coord_line, child_line in zip(open(coords_file, 'r'), open(child_file, 'r')):
        jack_num = int(child_line.split('\t')[0])
        split_line = coord_line.split()
        fs_coords = [float(f) for f in split_line]
        for lead in list(leads.values()):
            for contact in list(lead.values()):
                if contact.jack_num is not None and int(contact.jack_num) == int(jack_num):
                    contact.fs_coords = fs_coords
        
def build_leads(files, do_freesurfer=False):
    """
    Builds the leads dictionary from VOX_coords_mother and jacksheet
    :param files: dictionary of files including 'vox_mom' and 'jacksheet'
    :returns: dictionary of form {lead_name: {contact_name1: contact1, contact_name2:contact2, ...}}
    """
    leads = read_mother(files['vox_mom'])
    add_jacksheet(leads, files['jacksheet'])
    add_grid_loc(leads)
    if do_freesurfer:
        add_freesurfer_coords(leads, files)
    return leads

def file_locations(subject):
    """
    Creates the default file locations dictionary
    :param subject: Subject name to look for files within
    :returns: Dictionary of {file_name: file_location}
    """
    files = dict(
        vox_mom=os.path.join(paths.rhino_root, 'data', 'eeg', subject, 'tal', 'VOX_coords_mother.txt'),
        jacksheet=os.path.join(paths.rhino_root, 'data', 'eeg', subject, 'docs', 'jacksheet.txt'),
        fs_coords=os.path.join(paths.rhino_root, 'data', 'eeg', subject, 'tal', 'coords', 'indiv_monopolar_nosnap.txt'),
        vox_child=os.path.join(paths.rhino_root, 'data', 'eeg', subject, 'tal', 'VOX_coords.txt')
    )
    return files

def convert(files, output):
    leads = build_leads(files)
    leads_as_dict = leads_to_dict(leads)
    clean_json_dump(leads_as_dict, open(output, 'w'), indent=2, sort_keys=True)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--subject', dest='subject', required=True)
    parser.add_argument('-o', '--output', dest='output', required=True)
    parser.add_argument('--add-fs', dest='add_fs', required=False, action='store_true', default=False)
    args = parser.parse_args()
    leads = build_leads(file_locations(args.subject), args.add_fs)
    leads_as_dict = leads_to_dict(leads)
    clean_json_dump(leads_as_dict, open(args.output,'w'), indent=2, sort_keys=True)

