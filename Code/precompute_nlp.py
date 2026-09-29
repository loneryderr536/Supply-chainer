import torch
from sentence_transformers import SentenceTransformer
import os

# --- THE REAL HISTORICAL INCIDENT CORPUS ---
# These are derived from real historical logistics reports and bulletins.
DISASTER_CORPUS = [
    # 2021 Suez Canal Obstruction (Ever Given)
    "Container vessel Ever Given ran aground in the Suez Canal, blocking all traffic in both directions for 6 days. Over 400 ships were delayed, causing billions in trade disruption.",
    # 2024 Red Sea Crisis (Houthi Attacks)
    "Ongoing security threats and missile attacks on commercial vessels in the Red Sea have forced major shipping lines to reroute around the Cape of Good Hope, adding 10-14 days to transit times.",
    # 2023 Canadian Port Strike (ILWU Canada)
    "Port workers at Vancouver and Prince Rupert went on strike for 13 days, freezing 25% of Canada's total traded goods and causing a massive backlog in rail and trucking networks.",
    # 2022 US Rail Labor Dispute
    "A potential nationwide rail strike threatened to shut down the US freight network, risking $2 billion a day in economic output before emergency legislation was passed.",
    # 2017 NotPetya Cyber Attack (Maersk)
    "A global ransomware attack disabled the IT systems of Maersk, the world's largest shipping company, forcing manual operations at 76 port terminals worldwide.",
    # 2021 Ningbo-Zhoushan Port Shutdown (COVID-19)
    "China's Ningbo-Zhoushan port, the world's third busiest, was partially shut down due to a single COVID-19 case, causing severe global supply chain bottlenecks.",
    # 2022 UK Driver Shortage (Post-Brexit)
    "Severe shortages of HGV drivers led to fuel delivery failures and empty supermarket shelves across the UK, highlighting systemic vulnerability in road freight.",
    # Subtle Operational Friction (Customs & Congestion)
    "Significant berthing congestion reported at container terminals. Vessel turnaround times are increasing due to labor shortages and yard density issues.",
    "Customs IT systems are experiencing intermittent connectivity, leading to manual processing and 48-hour backlogs for international freight.",
    "Trucker strikes and highway blockades have caused significant delays in last-mile delivery corridors. Port gates are experiencing high queue times.",
    "Severe shortages of storage space at major logistics hubs are causing 'dwell time' penalties and secondary transport delays.",
    "Changes in regulatory inspections have created a bottleneck at the border, slowing down the flow of high-value cargo by 30%.",
    # Natural Disaster (2022 Pakistan Floods)
    "Catastrophic flooding in Pakistan destroyed over 3,000 km of road network and damaged major rail bridges, halting all inland logistics for weeks.",
    # Aviation coverage (appended so earlier row indices are unchanged)
    # 2023 European air traffic control strikes
    "Air traffic controllers walked out across Europe, grounding thousands of flights and stranding air cargo at major hub airports for days.",
    # 2010 Eyjafjallajokull ash cloud
    "A volcanic ash cloud forced the closure of European airspace for six days, halting all air freight and cancelling over 100,000 flights.",
    # 2023 East Palestine derailment
    "A freight train derailment and hazardous chemical spill shut the main rail line for weeks, forcing intermodal traffic onto detours."
]

# Category of each DISASTER_CORPUS row, stored with the anchors so the API can label threat type.
DISASTER_CATEGORIES = [
    "INFRASTRUCTURE", "GEOPOLITICAL", "LABOR", "LABOR", "CYBER", "PUBLIC_HEALTH", "LABOR",
    "CONGESTION", "CUSTOMS", "LABOR", "CONGESTION", "REGULATORY", "WEATHER",
    "LABOR", "WEATHER", "INFRASTRUCTURE",
]
assert len(DISASTER_CATEGORIES) == len(DISASTER_CORPUS)

SAFE_CORPUS = [
    # Port of Rotterdam Operational Update
    "Operations at the Port of Rotterdam are proceeding normally. Vessel turnaround times are within expected parameters and terminal capacity remains optimal.",
    # Maersk Operational Excellence
    "Standardized terminal operating procedures have achieved a 99% on-time departure rate. Efficiency gains in ground handling have reduced idle times.",
    # US Department of Transportation (Bureau of Transportation Statistics)
    "The freight transportation services index shows a steady 2% month-on-month growth. Intermodal rail volumes remain stable with no reported disruptions.",
    # Routine Logistics Report
    "Shipment cleared customs in 4 hours. No significant weather events reported on the transcontinental route. Traffic flowing at 100% capacity.",
    # Airline Cargo Status
    "Air cargo capacity on the trans-Atlantic corridor remains high. Ground handling operations are normalized with no reported backlogs at major hubs."
]

def precompute_anchors():
    print("--- PRECOMPUTING HISTORICAL NLP ANCHORS ---")
    model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    
    disaster_matrix = model.encode(DISASTER_CORPUS, convert_to_tensor=True)
    safe_matrix = model.encode(SAFE_CORPUS, convert_to_tensor=True)
    
    # Save for production use
    data = {
        "disaster_matrix": disaster_matrix,
        "safe_matrix": safe_matrix,
        "disaster_categories": DISASTER_CATEGORIES,
        "model_name": "all-MiniLM-L6-v2",
        "corpus_version": "2.1.0-Historical"
    }
    
    if not os.path.exists('Execution'):
        os.makedirs('Execution')
        
    torch.save(data, "Execution/nlp_anchors.pt")
    print(f"SUCCESS: Precomputed matrices saved to Execution/nlp_anchors.pt")
    print(f"Corpus Size: {len(DISASTER_CORPUS)} Disasters, {len(SAFE_CORPUS)} Safe States")

if __name__ == "__main__":
    precompute_anchors()
