import os
import pandas as pd

# For this version documentation will need to be drafted on:
    # Opening file location
    # Copy/Pasting file path into tool

# TMO test file is located @ \Data\TMO SC Report 10-22_TEST
# CSC test file is located @ \Data\CSC Report 10-22_TEST

# Enter file path as *** TMO SC Report 10-22_TEST.csv ***
# Enter file path as *** CSC Report 10-22_TEST.csv ***

# The objective of the Agg_input_logic function is to intake both TMO/CSC reports as .csv files and then convert and return them as pandas dataframe objects

def Agg_input_logic():
    
    def TMO_input():

        TMO_input = input("Enter the path of your TMO Report .csv file: ") # Test to validate input
     
        assert os.path.exists(TMO_input), "No file found at location: , "+str(TMO_input) # Test to validate specified file format is received
        f = open(TMO_input,'r+')

        TMO_df = pd.read_csv(TMO_input, index_col=0) # Test to validate pandas dataframe output
        
        #Sorts ComboList_df ascending by 'Short Code'
        
        #TMO_df_Sorted = TMO_df.sort_values(by='Start Application Addresses')
        
        #display(TMO_df_Sorted)
        return TMO_df # Test to validate pandas dataframe is returned

        
    def CSC_input():
        
        CSC_input = input("Enter the path of your CSC Report .csv file: ") # Test to validate input
     
        assert os.path.exists(CSC_input), "No file found at location: , "+str(CSC_input) # Test to validate specified file format is received
        f = open(CSC_input,'r+')

        CSC_df = pd.read_csv(CSC_input, index_col=0) # Test to validate pandas dataframe output
        
        #display(CSC_df)
        return CSC_df # Test to validate pandas dataframe is returned
        
    TMO = TMO_input()
    CSC = CSC_input()
    
    #display(TMO, CSC)
    
    Agg_Logic(TMO, CSC)
    #return TMO, CSC #remove for production (?)
  
        
# Create Agg_logic function

# The objective of the Agg_Logic function is to intake the TMO/CSC dataframes created in the Agg_input_logic function and use Pandas module to aggregate them as requested

def Agg_Logic(TMO, CSC): # Pass in (TMO, CSC) variables

# Create ComboList function to create new pandas Dataframe to hold TMO/CSC data
    
    # Merges TMO/CSC dataframes on 'Short Code'/'Start Application Addresses' columns
        
    merged_data_df = CSC.merge(TMO, left_on='Short Code', right_on='Start Application Addresses', how='left')
        
    data = merged_data_df
        
    #display(data)


    def ComboList(data):
        
        ComboList = {
            'Short Code': [],
            'Aggregator': [],
            'Content Provider': [],
            'Lease Holder': [],
        }
    
        ComboList_df = pd.DataFrame(ComboList) # Test that dataframe is created w/ correct column headings
        ComboList_df[['Short Code', 'Aggregator', 'Content Provider']] = TMO[['Start Application Addresses', 'Provider Name', 'Content Provider Name']].reset_index(drop=True)
        ComboList_df[['Lease Holder']] = CSC[['Company']].reset_index(drop=True) # Copies data from CSC into ComboList_df
        
        #display(ComboList_df)
        return ComboList_df

    ComboListFunc = ComboList(data)
    
    #display(ComboListFunc)
    return ComboListFunc
    

    #To Do:
    
    # Write function that:
        # Filters 'Start Application Addresses Column'
        # Removes duplicates
        # Removes 3, 4, 7, 8+ digit codes
        # Removes email addresses
        # Removes codes where 'Content Provider Name' column in NaN
    
    #Take data from 'Company' columns in CSC Report and move into ComboList_df under 'Lease Holder' column
    #Display/return updated ComboList_df
    #Sort Values by 'Short Codes' & Reset index *** .sort_values(by=['Start Application Addresses'], ascending=True) ***

    #Export as .csv



# Write tests to confirm run conditions and logic

# Save/Export 'CSCA/TMO Combined List' as .csv/.xlsx and prompt for download
